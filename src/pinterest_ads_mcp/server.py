"""Build the MCP server. Stays import-light so tests can patch pieces individually."""

from __future__ import annotations

import functools
import inspect
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, tools
from ._logging import configure_default, get_logger
from .audit import AuditLog
from .auth import OAuthError, load_auth, redact
from .client import PinterestAdsClient, PinterestAPIError
from .config import API_VERSION, Config, load_config
from .tool_catalog import ALWAYS_REGISTERED, TOOL_SPECS

logger = get_logger("server")

INSTRUCTIONS = """\
Pinterest API v5 (ads): ad accounts, campaigns, ad groups, ads, keywords, audiences, catalogs, analytics, reports.
Start with pinterest_ads_describe_server and pinterest_ads_health_check; pick an ad_account_id from pinterest_ads_list_ad_accounts.
Writes change live ads and spend. Pinterest requires the user to approve each change: rehearse in dry-run (pinterest_ads_set_dry_run) and show them what will change first.
Money fields are micro-units (1.00 = 1000000). New campaigns, ad groups and ads start PAUSED. Archives, deletes, big batches and bid or budget rises need confirm ('ARCHIVE', 'DELETE' or 'APPLY').
Don't combine one Pinterest account's data with another account's or other services' data beyond what this user asked for, and don't make benchmark or competitor claims.
Every write is in the audit log (pinterest_ads_audit_tail). Tools can be limited with PINTEREST_ADS_TOOL_GROUPS or PINTEREST_ADS_READ_ONLY=true."""

# Errors whose message helps the agent fix the call (bad arguments, a refusal,
# Pinterest API errors, token problems, permissions or config problems).
_EXPECTED_ERRORS = (
    ValueError,
    RuntimeError,
    PermissionError,
    FileNotFoundError,
    PinterestAPIError,
    httpx.HTTPError,
)


def _as_tool_error(fn):
    """Re-raise expected errors as ToolError so MCP SDK 2 shows their message.

    SDK 2 hides the text of any other exception from the client. Messages are
    redacted so tokens and secrets never reach the client.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except _EXPECTED_ERRORS as exc:
                raise ToolError(redact(str(exc))) from exc

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            raise ToolError(redact(str(exc))) from exc

    return wrapper


class PinterestAdsMCPServer(MCPServer):
    """MCPServer whose tools report expected errors to the client.

    Each tool gets its title and annotations from `tool_catalog.TOOL_SPECS`
    unless the decorator passes them. With `read_only=True`, tools that are not
    annotated read-only are skipped (the meta tools are always registered).
    """

    def __init__(self, *args: Any, read_only: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read_only = read_only

    def tool(  # type: ignore[override]
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        **kwargs: Any,
    ):
        def decorator(fn):
            tool_name = name or fn.__name__
            spec = TOOL_SPECS.get(tool_name)
            tool_title = title or (spec.title if spec else None)
            tool_annotations = annotations or (spec.annotations if spec else None)
            is_read_only = bool(tool_annotations and tool_annotations.read_only_hint)
            if self.read_only and not is_read_only and tool_name not in ALWAYS_REGISTERED:
                return fn
            register = super(PinterestAdsMCPServer, self).tool(
                name=tool_name,
                title=tool_title,
                description=description,
                annotations=tool_annotations,
                **kwargs,
            )
            register(_as_tool_error(fn))
            return fn

        return decorator


def build_server(config: Config | None = None, env_file: Path | None = None) -> MCPServer:
    """Construct the MCP server with every pinterest_ads_* tool registered."""
    configure_default()
    if config is None:
        config = load_config(env_file=env_file)
    groups = tools.resolve_tool_groups(config.tool_groups)  # fail fast on unknown groups

    auth = load_auth(config)
    audit = AuditLog(config.audit_log, retention_days=config.audit_retention_days)
    pruned = audit.prune()
    if pruned:
        logger.info(
            "pruned %d audit entries older than %d days", pruned, config.audit_retention_days
        )
    client = PinterestAdsClient(config, auth, audit)

    logger.info(
        "starting pinterest-ads-mcp api=%s ad_account=%s auth=%s dry_run=%s",
        config.api_base,
        config.ad_account_id or "-",
        auth.account_label,
        config.dry_run,
    )

    mcp = PinterestAdsMCPServer(
        "pinterest-ads-mcp",
        title="Pinterest Ads",
        version=__version__,
        instructions=INSTRUCTIONS,
        read_only=config.read_only,
    )

    @mcp.tool()
    def pinterest_ads_describe_server() -> dict[str, Any]:
        """Show which API host, default ad account and safety settings this server uses."""
        return {
            "version": __version__,
            "api_version": API_VERSION,
            "api_base": config.api_base,
            "sandbox": config.sandbox,
            "default_ad_account_id": config.ad_account_id,
            "auth": auth.account_label,
            "dry_run": config.dry_run,
            "read_only": config.read_only,
            "tool_groups": config.tool_groups or "all",
            "bulk_threshold": config.bulk_threshold,
            "max_increase_pct": config.max_increase_pct,
            "max_budget": config.max_budget,
            "max_bid": config.max_bid,
            "audit_log": str(config.audit_log),
            "audit_retention_days": config.audit_retention_days,
            "audit_snapshots": config.audit_snapshots,
            "max_download_mb": config.max_download_mb,
            "page_size": config.page_size,
            "timeout": config.timeout,
            "transport": config.transport,
        }

    @mcp.tool()
    def pinterest_ads_health_check() -> dict[str, Any]:
        """Check the setup: refresh the access token, read the signed-in account, then list
        the ad accounts it can use. Also reports granted scopes and the rate-limit headers."""
        checks: list[dict[str, Any]] = []
        notes: list[str] = []
        t0 = time.time()
        try:
            auth.invalidate()
            auth.access_token()
            checks.append({"check": "token", "ok": True, "ms": int((time.time() - t0) * 1000)})
        except (OAuthError, httpx.HTTPError) as e:
            checks.append({"check": "token", "ok": False, "error": redact(str(e))[:300]})
            return {"ok": False, "checks": checks}
        token_status = auth.status()
        days_left = token_status.get("refresh_token_days_left")
        if isinstance(days_left, int | float) and days_left < 14:
            notes.append(
                f"The refresh token expires in {days_left} days unless it is used; the server "
                "renews it on each start when it can save the token file."
            )
        user: dict[str, Any] = {}
        t0 = time.time()
        try:
            out = client.request("GET", "/user_account")
            user = out if isinstance(out, dict) else {}
            checks.append(
                {"check": "user_account", "ok": True, "ms": int((time.time() - t0) * 1000)}
            )
        except Exception as e:
            checks.append({"check": "user_account", "ok": False, "error": redact(str(e))[:300]})
        accounts: list[Any] = []
        listed = False
        t0 = time.time()
        try:
            accounts, _ = client.paginate("/ad_accounts", {}, max_results=100)
            listed = True
            checks.append(
                {"check": "list_ad_accounts", "ok": True, "ms": int((time.time() - t0) * 1000)}
            )
        except Exception as e:
            checks.append({"check": "list_ad_accounts", "ok": False, "error": redact(str(e))[:300]})
        summary = [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "currency": a.get("currency"),
                "country": a.get("country"),
                "permissions": a.get("permissions"),
            }
            for a in accounts
            if isinstance(a, dict)
        ]
        if config.ad_account_id and listed:
            visible = any(str(a.get("id")) == config.ad_account_id for a in summary)
            checks.append({"check": "default_ad_account_visible", "ok": visible})
        if listed and not accounts:
            notes.append(
                "No ad accounts visible. Check that the Pinterest user who ran "
                "`pinterest-ads-mcp auth` owns an ad account or has business access to one."
            )
        scopes = token_status.get("scopes") or []
        missing = [s for s in ("ads:read", "ads:write") if scopes and s not in scopes]
        if missing:
            notes.append(f"The token lacks {', '.join(missing)}; run `pinterest-ads-mcp auth`.")
        return {
            "ok": all(c["ok"] for c in checks),
            "api_base": config.api_base,
            "user": {k: user.get(k) for k in ("username", "account_type", "business_name")},
            "token": token_status,
            "checks": checks,
            "ad_accounts": summary,
            "rate_limit": client.last_rate_limit or None,
            "notes": notes,
        }

    @mcp.tool()
    def pinterest_ads_audit_lookup(audit_id: str) -> dict[str, Any]:
        """Look up an audit log entry by ID: the request, response summary and old values of a write."""
        entry = audit.find(audit_id)
        if not entry:
            return {"found": False, "audit_id": audit_id}
        return {"found": True, "entry": entry}

    @mcp.tool()
    def pinterest_ads_audit_tail(limit: int = 20) -> dict[str, Any]:
        """Show the most recent audit log entries (writes and dry-run rehearsals)."""
        entries = audit.tail(max(0, min(limit, 500)))
        return {"count": len(entries), "entries": entries}

    @mcp.tool()
    def pinterest_ads_set_dry_run(enabled: bool) -> dict[str, Any]:
        """Turn dry-run on or off for this session. In dry-run, writes are recorded in the
        audit log and returned as a preview but NOT sent to Pinterest; reads still work."""
        client.config.dry_run = enabled
        logger.info("dry_run=%s (set via tool)", enabled)
        return {"dry_run": enabled}

    for group in groups:
        getattr(tools, group).register(mcp, client)

    logger.info(
        "registered %d tools (groups=%s read_only=%s)",
        len(mcp._tool_manager.list_tools()),
        ",".join(config.tool_groups) or "all",
        config.read_only,
    )
    return mcp
