from __future__ import annotations

from pathlib import Path

import pytest

from pinterest_ads_mcp.config import (
    PRODUCTION_API,
    SANDBOX_API,
    load_config,
    token_file_path,
)


def _creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ADS_APP_ID", "1234567")
    monkeypatch.setenv("PINTEREST_ADS_APP_SECRET", "secret-for-tests")
    monkeypatch.setenv("PINTEREST_ADS_REFRESH_TOKEN", "pinr_testvalue")


def test_defaults_live_in_the_home_folder(
    monkeypatch: pytest.MonkeyPatch, _isolated_env: Path
) -> None:
    _creds(monkeypatch)
    config = load_config()
    assert config.token_file == _isolated_env / "secrets" / "pinterest-ads-token.json"
    assert config.audit_log == _isolated_env / "logs" / "audit.jsonl"
    assert config.api_base == PRODUCTION_API
    assert config.token_url == f"{PRODUCTION_API}/oauth/token"
    assert config.max_increase_pct == 20
    assert config.bulk_threshold == 10
    assert config.audit_retention_days == 30
    assert config.audit_snapshots is True
    assert config.max_download_bytes == 200 * 1024 * 1024
    assert config.transport == "stdio" and config.host == "127.0.0.1"
    assert config.allow_remote is False


def test_home_defaults_to_user_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PINTEREST_ADS_HOME")
    assert token_file_path() == Path.home() / ".pinterest-ads-mcp" / "secrets" / (
        "pinterest-ads-token.json"
    )


def test_missing_app_credentials_explain_where_to_find_them() -> None:
    with pytest.raises(RuntimeError, match="My apps"):
        load_config()


def test_missing_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ADS_APP_ID", "1234567")
    monkeypatch.setenv("PINTEREST_ADS_APP_SECRET", "secret-for-tests")
    with pytest.raises(RuntimeError, match="pinterest-ads-mcp auth"):
        load_config()
    assert load_config(require_credentials=False).refresh_token is None


def test_static_access_token_needs_no_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ADS_ACCESS_TOKEN", "pina_statictest")
    config = load_config()
    assert config.access_token == "pina_statictest"
    assert config.app_id is None


def test_refresh_token_from_token_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PINTEREST_ADS_APP_ID", "1234567")
    monkeypatch.setenv("PINTEREST_ADS_APP_SECRET", "secret-for-tests")
    token = tmp_path / "t.json"
    token.write_text('{"refresh_token": "pinr_fromfile"}')
    monkeypatch.setenv("PINTEREST_ADS_TOKEN_FILE", str(token))
    assert load_config().refresh_token == "pinr_fromfile"


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("PINTEREST_ADS_AD_ACCOUNT_ID", "abc", "numeric"),
        ("PINTEREST_ADS_PAGE_SIZE", "500", "between 1 and 250"),
        ("PINTEREST_ADS_TRANSPORT", "websocket", "TRANSPORT"),
        ("PINTEREST_ADS_MAX_BUDGET", "lots", "number"),
        ("PINTEREST_ADS_TOOL_GROUPS", "campaigns,nope", "nope"),
    ],
)
def test_bad_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str, message: str
) -> None:
    _creds(monkeypatch)
    monkeypatch.setenv(var, value)
    with pytest.raises(RuntimeError, match=message):
        load_config()


def test_sandbox_switches_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    _creds(monkeypatch)
    monkeypatch.setenv("PINTEREST_ADS_SANDBOX", "true")
    config = load_config()
    assert config.api_base == SANDBOX_API
    assert config.token_url == f"{SANDBOX_API}/oauth/token"


def test_env_files_are_read_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolated_env: Path
) -> None:
    _isolated_env.mkdir(parents=True, exist_ok=True)
    (_isolated_env / ".env").write_text(
        "PINTEREST_ADS_APP_ID=1111111\nPINTEREST_ADS_APP_SECRET=home-secret\n"
        "PINTEREST_ADS_REFRESH_TOKEN=pinr_home\nPINTEREST_ADS_AD_ACCOUNT_ID=222\n"
    )
    Path(".env").write_text("PINTEREST_ADS_AD_ACCOUNT_ID=333\n")
    config = load_config()
    assert config.app_id == "1111111"  # from ~/.pinterest-ads-mcp/.env
    assert config.ad_account_id == "333"  # ./.env wins over the home file


def test_explicit_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = tmp_path / "custom.env"
    env.write_text(
        "PINTEREST_ADS_APP_ID=9999999\nPINTEREST_ADS_APP_SECRET=s3cret-value\n"
        "PINTEREST_ADS_REFRESH_TOKEN=pinr_x\nPINTEREST_ADS_READ_ONLY=true\n"
    )
    Path(".env").write_text("PINTEREST_ADS_APP_ID=1\n")
    config = load_config(env_file=env)
    assert config.app_id == "9999999"
    assert config.read_only is True


def test_snapshots_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _creds(monkeypatch)
    monkeypatch.setenv("PINTEREST_ADS_AUDIT_SNAPSHOTS", "false")
    assert load_config().audit_snapshots is False


def test_tool_groups_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    _creds(monkeypatch)
    monkeypatch.setenv("PINTEREST_ADS_TOOL_GROUPS", "Reporting, ad-groups")
    assert load_config().tool_groups == ["ad_groups", "reporting"]
