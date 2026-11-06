from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from pinterest_ads_mcp import auth as auth_mod
from pinterest_ads_mcp.auth import (
    OAuthError,
    RefreshTokenAuth,
    StaticTokenAuth,
    exchange_code,
    load_auth,
    redact,
    save_token_file,
)
from pinterest_ads_mcp.config import Config

TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


def _auth(token_file: Path | None = None) -> RefreshTokenAuth:
    return RefreshTokenAuth(
        app_id="1234567",
        app_secret="super-secret-app-value",
        refresh_token="pinr_refreshone",
        token_url=TOKEN_URL,
        token_file=token_file,
    )


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@respx.mock
def test_refresh_uses_basic_auth_and_caches() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "pina_accessone",
                "expires_in": 2592000,
                "scope": "ads:read ads:write",
                "refresh_token": "pinr_refreshone",
                "refresh_token_expires_at": 1900000000,
            },
        )
    )
    a = _auth()
    assert a.access_token() == "pina_accessone"
    assert a.access_token() == "pina_accessone"
    assert route.call_count == 1
    request = route.calls.last.request
    expected = base64.b64encode(b"1234567:super-secret-app-value").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert _form(request) == {"grant_type": "refresh_token", "refresh_token": "pinr_refreshone"}
    assert a.scopes == ["ads:read", "ads:write"]
    assert a.status()["refresh_token_expires_at"] == 1900000000


@respx.mock
def test_expired_token_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "pina_first", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "pina_second", "expires_in": 3600}),
        ]
    )
    now = [1_000_000.0]
    monkeypatch.setattr(auth_mod.time, "time", lambda: now[0])
    a = _auth()
    assert a.access_token() == "pina_first"
    now[0] += 3600 - 60  # inside the margin before expiry
    assert a.access_token() == "pina_second"
    assert route.call_count == 2


@respx.mock
def test_invalidate_forces_refresh() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "pina_x", "expires_in": 3600})
    )
    a = _auth()
    a.access_token()
    a.invalidate()
    a.access_token()
    assert route.call_count == 2


@respx.mock
def test_refusal_is_redacted() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"code": 2, "message": "Authentication failed for pinr_refreshone"}
        )
    )
    with pytest.raises(OAuthError) as ei:
        _auth().access_token()
    msg = str(ei.value)
    assert "Authentication failed" in msg
    assert "refreshone" not in msg
    assert "pinterest-ads-mcp auth" in msg


@respx.mock
def test_rotated_refresh_token_is_saved(tmp_path: Path) -> None:
    token_file = tmp_path / "secrets" / "token.json"
    save_token_file(token_file, {"refresh_token": "pinr_refreshone", "created_at": "x"})
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "pina_x",
                "expires_in": 2592000,
                "refresh_token": "pinr_refreshtwo",
                "refresh_token_expires_at": 1900000000,
                "scope": "ads:read",
            },
        )
    )
    _auth(token_file).access_token()
    data = json.loads(token_file.read_text())
    assert data["refresh_token"] == "pinr_refreshtwo"
    assert data["refresh_token_expires_at"] == 1900000000
    assert data["created_at"] == "x"


def test_token_file_and_folder_are_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "token.json"
    save_token_file(path, {"refresh_token": "pinr_abc"})
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@respx.mock
def test_exchange_code_asks_for_a_continuous_refresh_token() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "pina_a", "refresh_token": "pinr_b", "expires_in": 1}
        )
    )
    out = exchange_code(
        code="CODE",
        redirect_uri="http://localhost:8765/callback",
        app_id="1234567",
        app_secret="super-secret-app-value",
        token_url=TOKEN_URL,
    )
    assert out["refresh_token"] == "pinr_b"
    form = _form(route.calls.last.request)
    assert form == {
        "grant_type": "authorization_code",
        "code": "CODE",
        "redirect_uri": "http://localhost:8765/callback",
        "continuous_refresh": "true",
    }


@respx.mock
def test_exchange_code_failure_is_explained() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"message": "bad code"}))
    with pytest.raises(OAuthError, match="redirect URI"):
        exchange_code(code="C", redirect_uri="r", app_id="1", app_secret="s", token_url=TOKEN_URL)


def test_redact_hides_tokens_and_secret_keys() -> None:
    auth_mod.register_secret("my-very-secret-app-secret")
    out = redact(
        {
            "message": "token pina_abcDEF123456 and my-very-secret-app-secret",
            "header": "Bearer abcdefghijklmnop",
            "basic": "Basic MTIzOjQ1Njc4OTAxMg==",
            "refresh_token": "pinr_zzz",
            "app_secret": "whatever",
            "nested": [{"Authorization": "Bearer pina_q"}],
            "code": 2,
        }
    )
    assert out["message"] == "token *** and ***"
    assert out["header"] == "Bearer ***"
    assert out["basic"] == "Basic ***"
    assert out["refresh_token"] == "***"
    assert out["app_secret"] == "***"
    assert out["nested"][0]["Authorization"] == "***"
    assert out["code"] == 2


def test_static_token_auth(config: Config) -> None:
    static = StaticTokenAuth("pina_statictokenvalue")
    assert static.access_token() == "pina_statictokenvalue"
    assert static.can_refresh is False
    static.invalidate()
    assert redact("x pina_statictokenvalue") == "x ***"
    config.access_token = "pina_statictokenvalue"
    assert isinstance(load_auth(config), StaticTokenAuth)
    config.access_token = None
    assert isinstance(load_auth(config), RefreshTokenAuth)
