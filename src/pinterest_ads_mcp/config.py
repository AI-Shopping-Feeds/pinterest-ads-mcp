"""Configuration from environment variables and .env files.

Local files live in a per-user folder, ``~/.pinterest-ads-mcp/`` by default
(``PINTEREST_ADS_HOME`` moves it), never in the install location: uvx installs
into a cache that can be wiped at any time.

* ``secrets/`` (0700) holds the refresh-token file (0600).
* ``logs/`` (0700) holds the audit log (0600).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

APP_DIR_NAME = ".pinterest-ads-mcp"
TOKEN_FILE_NAME = "pinterest-ads-token.json"
AUDIT_LOG_NAME = "audit.jsonl"
# "sse" (HTTP+SSE) is deprecated by the MCP spec; prefer "streamable-http".
TRANSPORTS = ("stdio", "streamable-http", "sse")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Pinterest REST API v5 hosts and the OAuth consent page.
API_VERSION = "v5"
PRODUCTION_API = "https://api.pinterest.com/v5"
SANDBOX_API = "https://api-sandbox.pinterest.com/v5"
AUTHORIZE_URL = "https://www.pinterest.com/oauth/"

# Scopes `pinterest-ads-mcp auth` asks for by default. pins:write is only needed
# for pinterest_ads_create_ad_pin; add it with `auth --scopes`.
DEFAULT_SCOPES = (
    "ads:read",
    "ads:write",
    "boards:read",
    "pins:read",
    "catalogs:read",
    "catalogs:write",
    "user_accounts:read",
    "biz_access:read",
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def app_home() -> Path:
    """The per-user folder (PINTEREST_ADS_HOME, default ~/.pinterest-ads-mcp)."""
    raw = os.environ.get("PINTEREST_ADS_HOME", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path.home() / APP_DIR_NAME


def secure_dir(path: Path) -> Path:
    """Create a folder (and parents) that only the owner can read (0700 on POSIX)."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def _path_or_none(raw: str | None) -> Path | None:
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def token_file_path() -> Path:
    """Where `pinterest-ads-mcp auth` stores the refresh token.

    PINTEREST_ADS_TOKEN_FILE, default ~/.pinterest-ads-mcp/secrets/pinterest-ads-token.json.
    """
    return _path_or_none(os.environ.get("PINTEREST_ADS_TOKEN_FILE")) or (
        app_home() / "secrets" / TOKEN_FILE_NAME
    )


def audit_log_path() -> Path:
    """The audit log (PINTEREST_ADS_AUDIT_LOG, default ~/.pinterest-ads-mcp/logs/audit.jsonl)."""
    return _path_or_none(os.environ.get("PINTEREST_ADS_AUDIT_LOG")) or (
        app_home() / "logs" / AUDIT_LOG_NAME
    )


def load_env(env_file: Path | None = None) -> None:
    """Load .env files without overriding variables that are already set.

    Order: the --env file if given, otherwise ./.env in the current folder; then
    ~/.pinterest-ads-mcp/.env if it exists.
    """
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        local = Path.cwd() / ".env"
        if local.is_file():
            load_dotenv(dotenv_path=local, override=False)
    home_env = app_home() / ".env"
    if home_env.is_file():
        load_dotenv(dotenv_path=home_env, override=False)


def parse_tool_groups(raw: str | None) -> list[str]:
    """Split a comma-separated PINTEREST_ADS_TOOL_GROUPS value and check every name.

    Names are case-insensitive and may use '-' for '_'. Empty means all groups.
    """
    from .tools import resolve_tool_groups  # local: tools imports this module

    names = [p.strip().lower().replace("-", "_") for p in (raw or "").split(",")]
    names = [n for n in names if n]
    return resolve_tool_groups(names) if names else []


def read_token_file(path: Path) -> dict[str, object]:
    """Return the contents of the token file written by `auth`, or {}."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Could not read the token file {path}: {e}") from e
    return data if isinstance(data, dict) else {}


@dataclass
class Config:
    app_id: str | None
    app_secret: str | None
    refresh_token: str | None
    audit_log: Path
    access_token: str | None = None
    ad_account_id: str | None = None
    token_file: Path | None = None
    sandbox: bool = False
    page_size: int = 100
    timeout: float = 30.0
    dry_run: bool = False
    log_level: str = "INFO"
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    allow_remote: bool = False
    # Tool modules to register; empty means all (see pinterest_ads_mcp.tools.TOOL_GROUPS).
    tool_groups: list[str] = field(default_factory=list)
    # Register only tools annotated read-only (plus the meta tools).
    read_only: bool = False
    # Writes touching more than this many entities need confirm='APPLY'.
    bulk_threshold: int = 10
    # Bid or budget increases above this percentage need confirm='APPLY'.
    max_increase_pct: float = 20.0
    # Optional absolute caps in the ad account's currency (not micro-units).
    max_budget: float | None = None
    max_bid: float | None = None
    # Audit entries older than this are pruned at startup; 0 keeps them.
    audit_retention_days: int = 30
    # Keep the old values of changed fields in the audit log.
    audit_snapshots: bool = True
    # Largest report or bulk file the server will download, in megabytes.
    max_download_mb: int = 200

    @property
    def api_base(self) -> str:
        return SANDBOX_API if self.sandbox else PRODUCTION_API

    @property
    def token_url(self) -> str:
        return f"{self.api_base}/oauth/token"

    @property
    def max_download_bytes(self) -> int:
        return max(1, self.max_download_mb) * 1024 * 1024


def _int(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default).strip() or default)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a whole number") from e


def _float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default).strip() or default)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a number") from e


def _float_or_none(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a number, got '{raw}'") from e


def load_config(env_file: Path | None = None, *, require_credentials: bool = True) -> Config:
    """Read configuration from the environment (and .env files).

    Raises RuntimeError with an actionable message on misconfiguration.
    """
    load_env(env_file)

    app_id = os.environ.get("PINTEREST_ADS_APP_ID", "").strip() or None
    app_secret = os.environ.get("PINTEREST_ADS_APP_SECRET", "").strip() or None
    access_token = os.environ.get("PINTEREST_ADS_ACCESS_TOKEN", "").strip() or None

    token_file = token_file_path()
    refresh_token = os.environ.get("PINTEREST_ADS_REFRESH_TOKEN", "").strip() or None
    if refresh_token is None:
        stored = read_token_file(token_file).get("refresh_token")
        refresh_token = str(stored) if stored else None

    if access_token is None and (not app_id or not app_secret):
        raise RuntimeError(
            "PINTEREST_ADS_APP_ID and PINTEREST_ADS_APP_SECRET must be set.\n"
            "  - Find them at developers.pinterest.com -> My apps -> Manage -> Configure\n"
            "    (App id and App secret key). The app must be approved for Trial access.\n"
            "  - Or set PINTEREST_ADS_ACCESS_TOKEN to a test token to try the server."
        )
    if access_token is None and refresh_token is None and require_credentials:
        raise RuntimeError(
            "No refresh token configured. Either:\n"
            "  - run `pinterest-ads-mcp auth` once (saves it to "
            f"{token_file}), or\n"
            "  - set PINTEREST_ADS_REFRESH_TOKEN (starts with 'pinr'), or\n"
            "  - set PINTEREST_ADS_TOKEN_FILE to the file `pinterest-ads-mcp auth` wrote."
        )

    ad_account_id = os.environ.get("PINTEREST_ADS_AD_ACCOUNT_ID", "").strip() or None
    if ad_account_id and not ad_account_id.isdigit():
        raise RuntimeError(f"PINTEREST_ADS_AD_ACCOUNT_ID must be numeric, got '{ad_account_id}'.")

    transport = os.environ.get("PINTEREST_ADS_TRANSPORT", "stdio").strip().lower() or "stdio"
    if transport not in TRANSPORTS:
        raise RuntimeError(
            f"PINTEREST_ADS_TRANSPORT must be one of {', '.join(TRANSPORTS)}, got '{transport}'."
        )

    page_size = _int("PINTEREST_ADS_PAGE_SIZE", "100")
    if not 1 <= page_size <= 250:
        raise RuntimeError("PINTEREST_ADS_PAGE_SIZE must be between 1 and 250.")

    return Config(
        app_id=app_id,
        app_secret=app_secret,
        refresh_token=refresh_token,
        access_token=access_token,
        ad_account_id=ad_account_id,
        audit_log=audit_log_path(),
        token_file=token_file,
        sandbox=_truthy(os.environ.get("PINTEREST_ADS_SANDBOX")),
        page_size=page_size,
        timeout=_float("PINTEREST_ADS_TIMEOUT", "30"),
        dry_run=_truthy(os.environ.get("PINTEREST_ADS_DRY_RUN")),
        log_level=os.environ.get("PINTEREST_ADS_LOG_LEVEL", "INFO").upper(),
        transport=transport,
        host=os.environ.get("PINTEREST_ADS_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int("PINTEREST_ADS_PORT", "8000"),
        allow_remote=_truthy(os.environ.get("PINTEREST_ADS_ALLOW_REMOTE")),
        tool_groups=parse_tool_groups(os.environ.get("PINTEREST_ADS_TOOL_GROUPS")),
        read_only=_truthy(os.environ.get("PINTEREST_ADS_READ_ONLY")),
        bulk_threshold=_int("PINTEREST_ADS_BULK_THRESHOLD", "10"),
        max_increase_pct=_float("PINTEREST_ADS_MAX_INCREASE_PCT", "20"),
        max_budget=_float_or_none("PINTEREST_ADS_MAX_BUDGET"),
        max_bid=_float_or_none("PINTEREST_ADS_MAX_BID"),
        audit_retention_days=_int("PINTEREST_ADS_AUDIT_RETENTION_DAYS", "30"),
        audit_snapshots=os.environ.get("PINTEREST_ADS_AUDIT_SNAPSHOTS", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        max_download_mb=_int("PINTEREST_ADS_MAX_DOWNLOAD_MB", "200"),
    )
