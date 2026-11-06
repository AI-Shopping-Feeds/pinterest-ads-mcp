"""CLI entry point exposed as the `pinterest-ads-mcp` console script.

Subcommands:

* `pinterest-ads-mcp run`       (default) start the MCP server.
* `pinterest-ads-mcp auth`      one-time Pinterest OAuth consent; saves the refresh token.
* `pinterest-ads-mcp purge`     delete the audit log and the stored refresh token.
* `pinterest-ads-mcp describe`  print the effective configuration (no secrets).
* `pinterest-ads-mcp version`   print the package version.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import click

from . import __version__
from ._logging import configure_default, get_logger
from .auth import exchange_code, redact, register_secret, save_token_file
from .config import (
    AUTHORIZE_URL,
    DEFAULT_SCOPES,
    LOOPBACK_HOSTS,
    TRANSPORTS,
    app_home,
    audit_log_path,
    load_config,
    load_env,
    token_file_path,
)
from .server import build_server

logger = get_logger("cli")


@click.group(invoke_without_command=True)
@click.option(
    "--env",
    "env_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a .env file. Defaults to ./.env, then ~/.pinterest-ads-mcp/.env.",
)
@click.option(
    "--transport",
    type=click.Choice(TRANSPORTS),
    default=None,
    help="Override PINTEREST_ADS_TRANSPORT. 'sse' is deprecated; use 'streamable-http'.",
)
@click.option("--host", default=None, help="Bind host for HTTP transports (default 127.0.0.1).")
@click.option(
    "--port", type=int, default=None, help="Bind port for HTTP transports (default 8000)."
)
@click.option(
    "--allow-remote",
    is_flag=True,
    default=False,
    help="Allow binding HTTP transports to a non-loopback address (no built-in sign-in!).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    env_file: Path | None,
    transport: str | None,
    host: str | None,
    port: int | None,
    allow_remote: bool,
) -> None:
    """Pinterest Ads MCP server."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        env_file=env_file, transport=transport, host=host, port=port, allow_remote=allow_remote
    )
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Start the MCP server (default subcommand)."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"))
    if ctx.obj.get("transport"):
        config.transport = ctx.obj["transport"]
    if ctx.obj.get("host"):
        config.host = ctx.obj["host"]
    if ctx.obj.get("port"):
        config.port = ctx.obj["port"]
    if ctx.obj.get("allow_remote"):
        config.allow_remote = True

    if config.transport != "stdio" and config.host not in LOOPBACK_HOSTS:
        if not config.allow_remote:
            raise click.ClickException(
                f"Refusing to serve on {config.host}: the HTTP transport has no sign-in, so "
                "anyone who can reach it could use your Pinterest ad accounts. Use 127.0.0.1, "
                "or set PINTEREST_ADS_ALLOW_REMOTE=true (or --allow-remote) behind your own "
                "authenticating proxy."
            )
        logger.warning(
            "binding to %s: the server has no authentication, so anyone who can reach "
            "this address can use your Pinterest ad accounts",
            config.host,
        )

    mcp = build_server(config=config)

    if config.transport == "stdio":
        mcp.run("stdio")
    elif config.transport == "streamable-http":
        logger.info("serving streamable HTTP on http://%s:%d/mcp", config.host, config.port)
        mcp.run("streamable-http", host=config.host, port=config.port)
    elif config.transport == "sse":
        logger.warning(
            "the sse transport (HTTP+SSE) is deprecated by the MCP spec; "
            "use --transport streamable-http instead"
        )
        mcp.run("sse", host=config.host, port=config.port)
    else:
        raise click.ClickException(f"Unknown transport: {config.transport}")


def authorization_url(*, app_id: str, redirect_uri: str, state: str, scopes: list[str]) -> str:
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(scopes),
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def parse_redirect(value: str, expected_state: str) -> str:
    """Pull the authorization code out of a pasted redirect URL (or accept a bare code).

    A pasted URL must carry the same `state` the consent link was made with.
    """
    value = value.strip()
    if not value:
        raise click.ClickException("No code given.")
    if "code=" not in value and "error=" not in value:
        return value
    query = parse_qs(urlparse(value).query or value.split("?", 1)[-1])
    if "error" in query:
        raise click.ClickException(f"Pinterest returned an error: {query['error'][0]}")
    state = query.get("state", [None])[0]
    if state is None:
        raise click.ClickException(
            "The pasted address has no state, so it can't be checked; start again."
        )
    if not secrets.compare_digest(state, expected_state):
        raise click.ClickException("The state in the redirect doesn't match; start again.")
    code = query.get("code", [""])[0]
    if not code:
        raise click.ClickException("No code in the redirect address.")
    return code


def _wait_for_code(port: int, path: str, expected_state: str, timeout: float) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path.rstrip("/") != path.rstrip("/"):
                self.send_response(404)
                self.end_headers()
                return
            state = query.get("state", [""])[0]
            if not secrets.compare_digest(state, expected_state):
                result["error"] = "state mismatch"
            elif "error" in query:
                result["error"] = query["error"][0]
            elif query.get("code"):
                result["code"] = query["code"][0]
            else:
                result["error"] = "no code in the redirect"
            ok = "code" in result
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = (
                "Authorised. You can close this window and return to the terminal."
                if ok
                else "Authorisation failed. Return to the terminal for details."
            )
            self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode())

        def log_message(self, *args: Any, **kwargs: Any) -> None:  # never log the code
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" in result:
        return result["code"]
    if "error" in result:
        raise click.ClickException(f"Authorisation failed: {result['error']}")
    raise click.ClickException("Timed out waiting for the browser redirect.")


@cli.command()
@click.option("--port", type=int, default=8765, help="Local callback port (default 8765).")
@click.option(
    "--manual", is_flag=True, help="Don't start a local server: paste the redirected address."
)
@click.option(
    "--redirect-uri",
    default=None,
    help="Redirect URI registered on your Pinterest app. Default http://localhost:PORT/callback.",
)
@click.option(
    "--scopes",
    default=None,
    help="Comma-separated scopes (default: " + ",".join(DEFAULT_SCOPES) + ").",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to save the refresh token (default PINTEREST_ADS_TOKEN_FILE or "
    "~/.pinterest-ads-mcp/secrets/pinterest-ads-token.json).",
)
@click.option("--no-browser", is_flag=True, help="Print the consent link instead of opening it.")
@click.option(
    "--show-token",
    is_flag=True,
    help="Also print the refresh token once (e.g. to paste into a client config).",
)
@click.option("--timeout", type=float, default=300, help="Seconds to wait for the redirect.")
@click.pass_context
def auth(
    ctx: click.Context,
    port: int,
    manual: bool,
    redirect_uri: str | None,
    scopes: str | None,
    output_path: Path | None,
    no_browser: bool,
    show_token: bool,
    timeout: float,
) -> None:
    """One-time Pinterest OAuth consent: get a refresh token for this server.

    Sign in with the Pinterest business account that owns, or has business access
    to, the ad accounts. The redirect URI must be listed exactly under Redirect
    URIs on your app at developers.pinterest.com (My apps -> Manage -> Configure).
    """
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"), require_credentials=False)
    if not config.app_id or not config.app_secret:
        raise click.ClickException(
            "Set PINTEREST_ADS_APP_ID and PINTEREST_ADS_APP_SECRET first (a static "
            "PINTEREST_ADS_ACCESS_TOKEN can't be used for consent)."
        )
    register_secret(config.app_secret)
    redirect = redirect_uri or f"http://localhost:{port}/callback"
    scope_list = [s.strip() for s in (scopes or ",".join(DEFAULT_SCOPES)).split(",") if s.strip()]
    state = secrets.token_urlsafe(24)
    url = authorization_url(
        app_id=config.app_id, redirect_uri=redirect, state=state, scopes=scope_list
    )
    click.echo(f"Redirect URI (must be listed on your app exactly): {redirect}")
    click.echo(f"Scopes: {', '.join(scope_list)}")
    click.echo("Open this link, sign in with the Pinterest business account, and Give access:")
    click.echo(url)

    if manual:
        if not no_browser:
            webbrowser.open(url)
        pasted = click.prompt(
            "Paste the full address you were redirected to (or just the code)", hide_input=True
        )
        code = parse_redirect(pasted, state)
    else:
        parsed = urlparse(redirect)
        if parsed.hostname not in LOOPBACK_HOSTS:
            raise click.ClickException(
                "Without --manual the redirect URI must be a localhost address."
            )
        if not no_browser:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        click.echo(f"Waiting for the redirect on {redirect} ...")
        code = _wait_for_code(parsed.port or port, parsed.path or "/", state, timeout)

    tokens = exchange_code(
        code=code,
        redirect_uri=redirect,
        app_id=config.app_id,
        app_secret=config.app_secret,
        token_url=config.token_url,
    )
    refresh = str(tokens["refresh_token"])
    register_secret(refresh)
    register_secret(str(tokens.get("access_token") or ""))
    expires_at = tokens.get("refresh_token_expires_at")
    if not expires_at and tokens.get("refresh_token_expires_in"):
        expires_at = int(time.time() + float(tokens["refresh_token_expires_in"]))
    target = output_path or config.token_file or token_file_path()
    save_token_file(
        target,
        {
            "refresh_token": refresh,
            "refresh_token_expires_at": expires_at,
            "scope": tokens.get("scope"),
            "sandbox": config.sandbox,
            "app_id_suffix": config.app_id[-4:],
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    click.echo(f"Saved the refresh token to {target} (owner-only permissions).")
    if target.resolve() != token_file_path().resolve():
        click.echo(f"Set PINTEREST_ADS_TOKEN_FILE={target.resolve()} so the server finds it.")
    if tokens.get("scope"):
        click.echo(f"Granted scopes: {tokens['scope']}")
    click.echo(
        "Next: run `pinterest-ads-mcp describe`, then add the server to your MCP client. "
        "Keep the token file out of git and never paste it into chat."
    )
    if show_token:
        click.echo(f"Refresh token: {refresh}")


@cli.command()
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@click.option("--keep-token", is_flag=True, help="Delete only the audit log.")
@click.pass_context
def purge(ctx: click.Context, yes: bool, keep_token: bool) -> None:
    """Delete locally stored Pinterest data: the audit log and the refresh-token file."""
    load_env(ctx.obj.get("env_file"))
    audit = audit_log_path()
    targets = [audit, audit.with_suffix(audit.suffix + ".tmp")]
    if not keep_token:
        targets.append(token_file_path())
    existing = [p for p in targets if p.exists()]
    if not existing:
        click.echo("Nothing to delete.")
        return
    click.echo("Will delete:\n" + "\n".join(f"  {p}" for p in existing))
    if not yes and not click.confirm("Delete these files?", default=False):
        click.echo("Cancelled.")
        return
    for p in existing:
        p.unlink()
    click.echo(f"Deleted {len(existing)} file(s).")
    if not keep_token:
        click.echo(
            "If you also set PINTEREST_ADS_REFRESH_TOKEN in a client config, remove it there. "
            "To revoke the app's access, remove it under Pinterest Settings -> Security and "
            "logins -> Apps (or reset the app secret at developers.pinterest.com)."
        )


@cli.command()
def version() -> None:
    """Print the pinterest-ads-mcp version."""
    click.echo(__version__)


@cli.command("describe")
@click.pass_context
def describe(ctx: click.Context) -> None:
    """Print the effective configuration (API host, ad account, paths, limits). No secrets."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"), require_credentials=False)
    if config.access_token:
        token = "static PINTEREST_ADS_ACCESS_TOKEN set"
    elif config.refresh_token:
        token = "set"
    else:
        token = "missing (run `pinterest-ads-mcp auth`)"
    info = {
        "version": __version__,
        "api_base": config.api_base,
        "sandbox": config.sandbox,
        "app_id": f"...{config.app_id[-4:]}" if config.app_id else None,
        "refresh_token": token,
        "home": str(app_home()),
        "token_file": str(config.token_file) if config.token_file else None,
        "default_ad_account_id": config.ad_account_id,
        "audit_log": str(config.audit_log),
        "audit_retention_days": config.audit_retention_days,
        "audit_snapshots": config.audit_snapshots,
        "dry_run": config.dry_run,
        "read_only": config.read_only,
        "tool_groups": config.tool_groups or "all",
        "bulk_threshold": config.bulk_threshold,
        "max_increase_pct": config.max_increase_pct,
        "max_budget": config.max_budget,
        "max_bid": config.max_bid,
        "max_download_mb": config.max_download_mb,
        "transport": config.transport,
        "host": config.host,
        "port": config.port,
        "allow_remote": config.allow_remote,
    }
    click.echo(json.dumps(info, indent=2, ensure_ascii=False))


def main() -> None:
    try:
        cli(obj={})
    except RuntimeError as e:
        # Surface config errors cleanly instead of a Python traceback.
        click.echo(f"Error: {redact(str(e))}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
