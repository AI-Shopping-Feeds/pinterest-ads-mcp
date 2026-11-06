"""OAuth 2.0 tokens for the Pinterest API v5.

`RefreshTokenAuth` swaps a continuous refresh token (60-day expiry, renewed on
every refresh) for access tokens (30-day expiry) and caches the access token in
memory only. When Pinterest returns a new refresh token, it is written back to
the token file (0600) so the next start uses it.

`StaticTokenAuth` uses a fixed access token (PINTEREST_ADS_ACCESS_TOKEN), such
as a 24-hour test token from the developer portal. It can't refresh.

Secrets are never logged: use `redact()` on anything that might contain one.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Config, secure_dir

# Refresh this many seconds before the access token's stated expiry.
EXPIRY_MARGIN = 300
# Access tokens last 30 days unless the response says otherwise.
DEFAULT_ACCESS_TTL = 30 * 86400

# Pinterest tokens start with pina_ (access), pinr_ (refresh) or pinc_ (client credentials).
_TOKEN_RE = re.compile(r"\bpin[acr]_[A-Za-z0-9\-._~+/=]{8,}")
_BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9\-._~+/=]{8,}", re.IGNORECASE)
_SECRET_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "app_secret",
    "authorization",
    "password",
    "conversion_token",
}
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value (e.g. the app secret) so redact() hides it."""
    if value and len(value) >= 8:
        _extra_secrets.add(value)


def redact(value: Any) -> Any:
    """Return a copy of value with tokens and known secrets replaced by '***'."""
    if isinstance(value, str):
        out = _TOKEN_RE.sub("***", value)
        out = _BEARER_RE.sub(lambda m: f"{m.group(1)} ***", out)
        for secret in _extra_secrets:
            out = out.replace(secret, "***")
        return out
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in _SECRET_KEYS and v else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class Auth(Protocol):
    @property
    def account_label(self) -> str: ...

    @property
    def can_refresh(self) -> bool: ...

    def access_token(self) -> str: ...

    def invalidate(self) -> None: ...

    def status(self) -> dict[str, Any]: ...


class OAuthError(RuntimeError):
    """Pinterest refused a token request (bad app credentials, revoked or expired token)."""


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text[:500]}


def save_token_file(path: Path, data: dict[str, Any]) -> None:
    """Write (or merge into) the token file with owner-only permissions (0600, folder 0700)."""
    secure_dir(path.parent)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    existing.update(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    if os.name == "posix":
        os.chmod(path, 0o600)


class RefreshTokenAuth:
    """Thread-safe refresh-token holder that caches access tokens until expiry."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        refresh_token: str,
        token_url: str,
        token_file: Path | None = None,
        timeout: float = 30.0,
    ):
        self.app_id = app_id
        self._app_secret = app_secret
        self._refresh_token = refresh_token
        self.token_url = token_url
        self.token_file = token_file
        self._timeout = timeout
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self.scopes: list[str] = []
        self.refresh_token_expires_at: int | None = None
        register_secret(app_secret)
        register_secret(refresh_token)

    @property
    def account_label(self) -> str:
        return f"app:...{self.app_id[-4:]}"

    @property
    def can_refresh(self) -> bool:
        return True

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def status(self) -> dict[str, Any]:
        expires = self.refresh_token_expires_at
        return {
            "type": "refresh_token",
            "scopes": self.scopes,
            "refresh_token_expires_at": expires,
            "refresh_token_days_left": (
                round((expires - time.time()) / 86400, 1) if expires else None
            ),
        }

    def access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - EXPIRY_MARGIN:
                return self._token
            try:
                resp = httpx.post(
                    self.token_url,
                    auth=(self.app_id, self._app_secret),
                    data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                    headers={"Accept": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                raise OAuthError(f"Could not reach the Pinterest token endpoint: {e}") from e
            payload = _safe_json(resp)
            if resp.status_code != 200 or not isinstance(payload, dict):
                raise OAuthError(
                    f"Pinterest refused the token refresh (HTTP {resp.status_code}): "
                    f"{redact(payload)}. If the refresh token expired (60 days without use) "
                    "or was revoked, run `pinterest-ads-mcp auth` again."
                )
            token = payload.get("access_token")
            if not token:
                raise OAuthError("Pinterest returned no access token.")
            self._token = str(token)
            register_secret(self._token)
            try:
                ttl = float(payload.get("expires_in") or DEFAULT_ACCESS_TTL)
            except (TypeError, ValueError):
                ttl = float(DEFAULT_ACCESS_TTL)
            self._expires_at = time.time() + ttl
            scope = payload.get("scope")
            if isinstance(scope, str):
                self.scopes = sorted(s for s in re.split(r"[ ,]+", scope) if s)
            expires_at = payload.get("refresh_token_expires_at")
            if isinstance(expires_at, int | float):
                self.refresh_token_expires_at = int(expires_at)
            new_refresh = payload.get("refresh_token")
            if new_refresh and new_refresh != self._refresh_token:
                self._refresh_token = str(new_refresh)
                register_secret(self._refresh_token)
                if self.token_file is not None and self.token_file.exists():
                    save_token_file(
                        self.token_file,
                        {
                            "refresh_token": self._refresh_token,
                            "refresh_token_expires_at": self.refresh_token_expires_at,
                            "scope": " ".join(self.scopes),
                        },
                    )
            return self._token


class StaticTokenAuth:
    """A fixed access token (for example a test token). It can't be refreshed."""

    def __init__(self, token: str):
        self._token = token
        register_secret(token)

    @property
    def account_label(self) -> str:
        return "static-access-token"

    @property
    def can_refresh(self) -> bool:
        return False

    def access_token(self) -> str:
        return self._token

    def invalidate(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {"type": "static_access_token", "scopes": None, "refresh_token_expires_at": None}


def exchange_code(
    *, code: str, redirect_uri: str, app_id: str, app_secret: str, token_url: str
) -> dict[str, Any]:
    """Swap a one-time authorization code for access and refresh tokens.

    Asks for a continuous refresh token (required for apps created before
    25 September 2025; newer apps get one anyway).
    """
    try:
        resp = httpx.post(
            token_url,
            auth=(app_id, app_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "continuous_refresh": "true",
            },
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the Pinterest token endpoint: {e}") from e
    payload = _safe_json(resp)
    if resp.status_code != 200 or not isinstance(payload, dict) or "refresh_token" not in payload:
        raise OAuthError(
            f"Pinterest refused the authorization code (HTTP {resp.status_code}): "
            f"{redact(payload)}. Codes are single-use and short-lived; the redirect URI must "
            "match the app settings exactly. Run the command again."
        )
    return payload


def load_auth(config: Config) -> Auth:
    if config.access_token:
        return StaticTokenAuth(config.access_token)
    if not config.refresh_token or not config.app_id or not config.app_secret:
        raise RuntimeError("No refresh token configured. Run `pinterest-ads-mcp auth` first.")
    return RefreshTokenAuth(
        app_id=config.app_id,
        app_secret=config.app_secret,
        refresh_token=config.refresh_token,
        token_url=config.token_url,
        token_file=config.token_file,
        timeout=config.timeout,
    )
