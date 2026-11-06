"""CLI: transports and the remote-binding guard, the auth helpers, purge and describe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import click
import pytest
from click.testing import CliRunner

from pinterest_ads_mcp import cli


class _FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, transport: str, **kwargs: Any) -> None:
        self.calls.append((transport, kwargs))


@pytest.fixture
def base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PINTEREST_ADS_APP_ID", "1234567")
    monkeypatch.setenv("PINTEREST_ADS_APP_SECRET", "cli-secret-value")
    monkeypatch.setenv("PINTEREST_ADS_REFRESH_TOKEN", "pinr_clirefresh")
    monkeypatch.setenv("PINTEREST_ADS_AUDIT_LOG", str(tmp_path / "logs" / "audit.jsonl"))
    monkeypatch.setenv("PINTEREST_ADS_TOKEN_FILE", str(tmp_path / "secrets" / "token.json"))
    return tmp_path


@pytest.fixture
def fake_run(base_env: Path, monkeypatch: pytest.MonkeyPatch):
    server = _FakeServer()
    monkeypatch.setattr(cli, "build_server", lambda config: server)
    warnings: list[str] = []
    monkeypatch.setattr(cli.logger, "warning", lambda msg, *args: warnings.append(msg % args))

    def invoke(*args: str, expect_ok: bool = True):
        env_file = str(base_env / "missing.env")
        result = CliRunner().invoke(cli.cli, ["--env", env_file, *args, "run"], obj={})
        if expect_ok:
            assert result.exit_code == 0, result.output
        return result, server.calls, warnings

    return invoke


def test_stdio_is_the_default(fake_run) -> None:
    _, calls, warnings = fake_run()
    assert calls == [("stdio", {})]
    assert warnings == []


def test_streamable_http_gets_host_and_port(fake_run) -> None:
    _, calls, _ = fake_run("--transport", "streamable-http", "--port", "9123")
    assert calls == [("streamable-http", {"host": "127.0.0.1", "port": 9123})]


def test_non_loopback_host_is_refused(fake_run) -> None:
    result, calls, _ = fake_run(
        "--transport", "streamable-http", "--host", "0.0.0.0", expect_ok=False
    )
    assert result.exit_code != 0
    assert "Refusing to serve on 0.0.0.0" in result.output
    assert calls == []


def test_allow_remote_flag_warns(fake_run) -> None:
    _, calls, warnings = fake_run(
        "--transport", "streamable-http", "--host", "0.0.0.0", "--allow-remote"
    )
    assert calls == [("streamable-http", {"host": "0.0.0.0", "port": 8000})]
    assert any("no authentication" in w for w in warnings)


def test_allow_remote_env(fake_run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ADS_ALLOW_REMOTE", "true")
    _, calls, _ = fake_run("--transport", "streamable-http", "--host", "10.0.0.5")
    assert calls[0][1]["host"] == "10.0.0.5"


def test_sse_warns_deprecated(fake_run) -> None:
    _, calls, warnings = fake_run("--transport", "sse")
    assert calls[0][0] == "sse"
    assert any("deprecated" in w for w in warnings)


def test_authorization_url() -> None:
    url = cli.authorization_url(
        app_id="1234567",
        redirect_uri="http://localhost:8765/callback",
        state="st",
        scopes=["ads:read", "ads:write"],
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://www.pinterest.com/oauth/"
    q = parse_qs(parsed.query)
    assert q["scope"] == ["ads:read,ads:write"]
    assert q["response_type"] == ["code"]
    assert q["state"] == ["st"]
    assert q["client_id"] == ["1234567"]


def test_parse_redirect_requires_matching_state() -> None:
    url = "http://localhost:8765/callback?code=ABC&state=st"
    assert cli.parse_redirect(url, "st") == "ABC"
    assert cli.parse_redirect("  ABC  ", "st") == "ABC"  # a bare code is allowed
    with pytest.raises(click.ClickException, match="doesn't match"):
        cli.parse_redirect("http://localhost/?code=ABC&state=other", "st")
    with pytest.raises(click.ClickException, match="no state"):
        cli.parse_redirect("http://localhost/?code=ABC", "st")
    with pytest.raises(click.ClickException, match="error"):
        cli.parse_redirect("http://localhost/?error=access_denied&state=st", "st")
    with pytest.raises(click.ClickException, match="No code given"):
        cli.parse_redirect("   ", "st")


def test_auth_manual_flow_saves_token_without_printing_it(
    base_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "fixedstate")
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    seen: dict[str, Any] = {}

    def fake_exchange(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "refresh_token": "pinr_brandnew",
            "access_token": "pina_x",
            "scope": "ads:read ads:write",
            "refresh_token_expires_in": 5184000,
        }

    monkeypatch.setattr(cli, "exchange_code", fake_exchange)
    result = CliRunner().invoke(
        cli.cli,
        ["--env", str(base_env / "missing.env"), "auth", "--manual", "--no-browser"],
        input="http://localhost:8765/callback?code=THECODE&state=fixedstate\n",
        obj={},
    )
    assert result.exit_code == 0, result.output
    assert seen["code"] == "THECODE"
    assert seen["redirect_uri"] == "http://localhost:8765/callback"
    assert seen["token_url"] == "https://api.pinterest.com/v5/oauth/token"
    saved = json.loads((base_env / "secrets" / "token.json").read_text())
    assert saved["refresh_token"] == "pinr_brandnew"
    assert saved["refresh_token_expires_at"] > 0
    assert "brandnew" not in result.output
    assert "cli-secret-value" not in result.output
    assert "scope=ads%3Aread" in result.output


def test_auth_needs_app_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PINTEREST_ADS_ACCESS_TOKEN", "pina_static")
    result = CliRunner().invoke(
        cli.cli, ["--env", str(tmp_path / "x.env"), "auth", "--manual"], obj={}
    )
    assert result.exit_code != 0
    assert "PINTEREST_ADS_APP_ID" in result.output


def test_auth_local_mode_needs_localhost(base_env: Path) -> None:
    result = CliRunner().invoke(
        cli.cli,
        [
            "--env",
            str(base_env / "missing.env"),
            "auth",
            "--no-browser",
            "--redirect-uri",
            "https://example.com/cb",
        ],
        obj={},
    )
    assert result.exit_code != 0
    assert "localhost" in result.output


def test_purge_deletes_audit_and_token(base_env: Path) -> None:
    audit = base_env / "logs" / "audit.jsonl"
    token = base_env / "secrets" / "token.json"
    audit.parent.mkdir(parents=True)
    token.parent.mkdir(parents=True)
    audit.write_text("{}\n")
    token.write_text("{}")
    result = CliRunner().invoke(
        cli.cli, ["--env", str(base_env / "missing.env"), "purge", "--yes"], obj={}
    )
    assert result.exit_code == 0, result.output
    assert not audit.exists() and not token.exists()


def test_purge_keep_token(base_env: Path) -> None:
    audit = base_env / "logs" / "audit.jsonl"
    token = base_env / "secrets" / "token.json"
    audit.parent.mkdir(parents=True)
    token.parent.mkdir(parents=True)
    audit.write_text("{}\n")
    token.write_text("{}")
    CliRunner().invoke(
        cli.cli,
        ["--env", str(base_env / "missing.env"), "purge", "--yes", "--keep-token"],
        obj={},
    )
    assert not audit.exists() and token.exists()


def test_describe_hides_secrets(base_env: Path) -> None:
    result = CliRunner().invoke(
        cli.cli, ["--env", str(base_env / "missing.env"), "describe"], obj={}
    )
    assert result.exit_code == 0, result.output
    assert "cli-secret-value" not in result.output
    assert "clirefresh" not in result.output
    info = json.loads(result.output)
    assert info["refresh_token"] == "set"
    assert info["app_id"] == "...4567"


def test_unknown_transport_is_rejected() -> None:
    result = CliRunner().invoke(cli.cli, ["--transport", "websocket", "run"], obj={})
    assert result.exit_code != 0
    assert "streamable-http" in result.output


def test_version() -> None:
    from pinterest_ads_mcp import __version__

    result = CliRunner().invoke(cli.cli, ["version"], obj={})
    assert result.output.strip() == __version__
