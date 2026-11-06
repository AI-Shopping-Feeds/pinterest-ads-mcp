"""Thin HTTP client for the Pinterest API v5.

Every API call goes through `PinterestAdsClient.request`, the one choke point
for auth headers, retries and backoff, rate-limit pacing, dry-run, the audit
log and redaction. It only talks to the configured API host
(api.pinterest.com or api-sandbox.pinterest.com) and, for report and bulk files,
the storage hosts Pinterest hands out signed links for.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
import time
import zipfile
import zlib
from typing import Any
from urllib.parse import urlparse

import httpx

from . import __version__
from ._logging import get_logger
from .audit import AuditLog
from .auth import Auth, redact
from .config import Config

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_BACKOFF = 120.0  # cap on honouring a long Retry-After / x-ratelimit-reset
MAX_PACE = 60.0  # longest pause when the rate-limit window is used up
MAX_RESULTS = 10_000  # most items one list call collects, to bound memory and API use
# Report and bulk files come from Pinterest or its storage provider (signed S3 links).
DOWNLOAD_HOST_SUFFIXES = (".pinterest.com", ".pinimg.com", ".amazonaws.com")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./]*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

logger = get_logger("client")


class PinterestAPIError(Exception):
    def __init__(self, status: int, body: Any, message: str = ""):
        self.status = status
        self.body = redact(body)
        super().__init__(message or self._message())

    def _message(self) -> str:
        detail = self.body
        if isinstance(self.body, dict):
            code = self.body.get("code")
            text = self.body.get("message") or self.body.get("_raw") or ""
            detail = f"code {code}: {text}" if code is not None else text or self.body
        msg = f"Pinterest API error {self.status}: {detail}"
        if self.status == 401:
            msg += " (the token is invalid or expired; run `pinterest-ads-mcp auth` again)"
        elif self.status == 403 and "PINNER_DATA_ACCESS_DENIED" in str(self.body):
            msg += " (this app isn't approved to read this Pinner's data; don't retry)"
        elif self.status == 403:
            msg += (
                " (the token lacks a scope or the user lacks access to this ad account; "
                "check the scopes with pinterest_ads_health_check)"
            )
        elif self.status == 429:
            msg += " (rate limit reached; wait before trying again)"
        return msg


def segment(value: Any, name: str = "id") -> str:
    """Validate one path segment (an ID) so it can't change the request path."""
    text = str(value).strip()
    if not _SEGMENT_RE.match(text):
        raise ValueError(f"{name} must be letters, digits, '-' or '_', got {text!r}.")
    return text


def check_path(path: str) -> str:
    """Validate a path relative to the API base (e.g. '/ad_accounts')."""
    if path.startswith("/v5/"):
        path = path[3:]
    if not _PATH_RE.match(path) or ".." in path or "//" in path or path.rstrip("/") in {"", "/"}:
        raise ValueError(
            "path must be relative to the Pinterest API v5 base and start with '/', e.g. "
            "'/ad_accounts'. Pass query parameters separately; '..', '//', '?' and '#' "
            "aren't allowed."
        )
    if path.startswith("/oauth"):
        raise ValueError("OAuth endpoints can't be called through this server.")
    return path


def summarise_response(payload: Any) -> Any:
    """A compact record of a write response for the audit log: IDs, statuses, errors."""
    if isinstance(payload, list):
        return {"count": len(payload)}
    if not isinstance(payload, dict):
        return None
    out: dict[str, Any] = {}
    items = payload.get("items")
    if isinstance(items, list):
        summary = []
        for it in items[:200]:
            data = it.get("data") if isinstance(it, dict) and "data" in it else it
            entry: dict[str, Any] = {}
            if isinstance(data, dict):
                for key in ("id", "status", "item_id"):
                    if key in data:
                        entry[key] = data[key]
            if isinstance(it, dict) and it.get("exceptions"):
                entry["exceptions"] = it["exceptions"]
            summary.append(entry)
        out["items"] = summary
    for key in ("id", "status", "request_id", "batch_id", "token", "report_status"):
        if key in payload and not isinstance(payload[key], dict | list):
            out[key] = payload[key]
    keywords = payload.get("keywords")
    if isinstance(keywords, list):
        out["keywords"] = [
            {"id": k.get("id"), "archived": k.get("archived")}
            for k in keywords[:500]
            if isinstance(k, dict)
        ]
    for key in ("errors", "exceptions"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            out[key] = value[:50]
    if not out:
        out["keys"] = sorted(str(k) for k in payload)[:20]
    return out


class PinterestAdsClient:
    def __init__(self, config: Config, auth: Auth, audit: AuditLog):
        self.config = config
        self.auth = auth
        self.audit = audit
        self._pause_until = 0.0
        self.last_rate_limit: dict[str, str] = {}
        self._http = httpx.Client(
            timeout=config.timeout,
            headers={"User-Agent": f"pinterest-ads-mcp/{__version__}"},
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PinterestAdsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return self.config.api_base

    def resolve_ad_account(self, ad_account_id: str | int | None) -> str:
        aid = str(ad_account_id).strip() if ad_account_id not in (None, "") else None
        aid = aid or self.config.ad_account_id
        if not aid:
            raise ValueError(
                "No ad_account_id. Pass ad_account_id (see pinterest_ads_list_ad_accounts) or "
                "set PINTEREST_ADS_AD_ACCOUNT_ID."
            )
        if not aid.isdigit():
            raise ValueError(f"ad_account_id must be numeric, got '{aid}'.")
        return aid

    def account_path(self, ad_account_id: str | int | None, *parts: str) -> str:
        """Build /ad_accounts/{id}/<parts...> with a validated ad account ID."""
        aid = self.resolve_ad_account(ad_account_id)
        return "/".join(["", "ad_accounts", aid, *parts]).rstrip("/")

    # ------------------------------------------------------------------ requests

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        ad_account_id: str | None = None,
        op: str | None = None,
        before: Any | None = None,
        read_only: bool = False,
        audit_body: Any | None = None,
    ) -> Any:
        """Perform one API request and return parsed JSON ({} for an empty body).

        path: relative to the API base, starting with '/'.
        read_only: True for POST endpoints that don't change anything (report
            requests, audience sizing, catalog item lookups). They bypass dry-run
            and aren't audited.
        audit_body: what to record instead of json_body (e.g. a count instead of
            customer-list records).
        """
        method = method.upper()
        path = check_path(path)
        url = f"{self.base_url}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != []}
        is_write = method in WRITE_METHODS and not read_only
        recorded_body = audit_body if audit_body is not None else json_body
        snapshot = before if self.config.audit_snapshots else None

        if is_write and self.config.dry_run:
            audit_id = self.audit.record(
                op=op or f"{method} {path}",
                resource=path,
                method=method,
                url=url,
                ad_account_id=ad_account_id,
                request_body=recorded_body,
                before=snapshot,
                dry_run=True,
            )
            logger.info("dry-run %s %s (audit=%s)", method, path, audit_id)
            return {
                "_dry_run": True,
                "method": method,
                "url": url,
                "params": clean_params or None,
                "body": recorded_body,
                "audit_id": audit_id,
            }

        last_exc: Exception | None = None
        refreshed = False
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            self._pace()
            headers = {
                "Authorization": f"Bearer {self.auth.access_token()}",
                "Accept": "application/json",
            }
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = self._http.request(
                    method,
                    url,
                    params=clean_params or None,
                    content=None if json_body is None else json.dumps(json_body),
                    headers=headers,
                )
            except httpx.RequestError as e:
                last_exc = e
                logger.warning("transport error attempt=%d %s %s: %s", attempt + 1, method, path, e)
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            self._note_rate_limit(resp)

            if resp.status_code == 401 and not refreshed and self.auth.can_refresh:
                # The cached access token may have been revoked early; refresh once.
                refreshed = True
                self.auth.invalidate()
                continue

            if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1:
                logger.warning(
                    "retryable status=%d attempt=%d %s %s",
                    resp.status_code,
                    attempt + 1,
                    method,
                    path,
                )
                self._sleep_backoff(attempt, resp)
                attempt += 1
                continue

            payload: Any = self._safe_json(resp)

            if not (200 <= resp.status_code < 300):
                if is_write:
                    self.audit.record(
                        op=op or f"{method} {path}",
                        resource=path,
                        method=method,
                        url=url,
                        ad_account_id=ad_account_id,
                        request_body=recorded_body,
                        response_status=resp.status_code,
                        response_summary=summarise_response(payload),
                        before=snapshot,
                        error=f"HTTP {resp.status_code}",
                    )
                logger.error(
                    "api error %d %s %s: %s", resp.status_code, method, path, redact(payload)
                )
                raise PinterestAPIError(resp.status_code, payload)

            if is_write:
                audit_id = self.audit.record(
                    op=op or f"{method} {path}",
                    resource=path,
                    method=method,
                    url=url,
                    ad_account_id=ad_account_id,
                    request_body=recorded_body,
                    response_status=resp.status_code,
                    response_summary=summarise_response(payload),
                    before=snapshot,
                )
                logger.info(
                    "write %s %s -> %d (audit=%s)", method, path, resp.status_code, audit_id
                )
                if isinstance(payload, dict):
                    payload = {**payload, "audit_id": audit_id}
                else:
                    payload = {"result": payload, "audit_id": audit_id}
            else:
                logger.debug("read  %s %s -> %d", method, path, resp.status_code)
            return payload

        raise PinterestAPIError(0, str(last_exc) if last_exc else "exhausted retries")

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_results: int = 200,
        items_key: str = "items",
    ) -> tuple[list[Any], bool]:
        """Follow `bookmark` on GET list endpoints. Returns (items, truncated)."""
        params = dict(params or {})
        items: list[Any] = []
        max_results = max(1, min(max_results, MAX_RESULTS))
        while True:
            params["page_size"] = max(1, min(self.config.page_size, max_results - len(items)))
            page = self.request("GET", path, params=params)
            batch = page.get(items_key) if isinstance(page, dict) else page
            items.extend(batch or [])
            bookmark = page.get("bookmark") if isinstance(page, dict) else None
            if not bookmark:
                return items, False
            if len(items) >= max_results:
                return items[:max_results], True
            params["bookmark"] = bookmark

    def snapshot(self, fn: Any, *args: Any, **kwargs: Any) -> Any | None:
        """Run a read for a before-snapshot; returns None instead of raising."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning("before-snapshot failed: %s", redact(str(e)))
            return None

    # ------------------------------------------------------------------ files

    def download(self, url: str) -> bytes:
        """Fetch a report or bulk file (a short-lived signed link) into memory.

        No auth header is sent. The file is capped at PINTEREST_ADS_MAX_DOWNLOAD_MB
        (after decompression) and is never written to disk.
        """
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host.endswith(DOWNLOAD_HOST_SUFFIXES):
            raise ValueError(f"Refusing to download from a host that isn't Pinterest's: {host!r}")
        cap = self.config.max_download_bytes
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with httpx.stream(
                    "GET",
                    url,
                    timeout=max(self.config.timeout, 120.0),
                    follow_redirects=False,
                    headers={"User-Agent": f"pinterest-ads-mcp/{__version__}"},
                ) as resp:
                    if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1:
                        self._sleep_backoff(attempt, resp)
                        continue
                    if resp.status_code != 200:
                        resp.read()
                        raise PinterestAPIError(resp.status_code, resp.text[:300])
                    declared = resp.headers.get("Content-Length")
                    if declared and declared.isdigit() and int(declared) > cap:
                        raise ValueError(self._too_big())
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > cap:
                            raise ValueError(self._too_big())
                    return self._unpack(bytes(buf), cap)
            except httpx.RequestError as e:
                last = e
                self._sleep_backoff(attempt)
        raise PinterestAPIError(0, str(last) if last else "download failed")

    def _too_big(self) -> str:
        return (
            f"The file is larger than PINTEREST_ADS_MAX_DOWNLOAD_MB ({self.config.max_download_mb} "
            "MB). Narrow the date range, filters or columns, or raise the limit."
        )

    def _unpack(self, raw: bytes, cap: int) -> bytes:
        if raw[:2] == b"\x1f\x8b":
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            out = d.decompress(raw, cap + 1)
            if len(out) > cap or d.unconsumed_tail:
                raise ValueError(self._too_big())
            return out
        if raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                members = [m for m in zf.infolist() if not m.is_dir()]
                if not members:
                    return b""
                if sum(m.file_size for m in members) > cap:
                    raise ValueError(self._too_big())
                return b"\n".join(zf.read(m) for m in members)
        return raw

    # ------------------------------------------------------------------ helpers

    def _pace(self) -> None:
        wait = self._pause_until - time.monotonic()
        if wait > 0:
            logger.info("rate-limit window used up; waiting %.1fs", wait)
            time.sleep(wait)

    def _note_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-remaining")
        reset = resp.headers.get("x-ratelimit-reset")
        if remaining is None:
            return
        self.last_rate_limit = {
            "limit": resp.headers.get("x-ratelimit-limit", ""),
            "remaining": remaining,
            "reset": reset or "",
        }
        try:
            left = int(remaining.split(",")[0].strip())
        except ValueError:
            return
        if left <= 0:
            self._pause_until = time.monotonic() + min(_seconds(reset) or 1.0, MAX_PACE)

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"_raw": resp.text[:2000]}

    @staticmethod
    def _sleep_backoff(attempt: int, resp: httpx.Response | None = None) -> None:
        wait_for = 0.0
        if resp is not None:
            wait_for = max(
                _seconds(resp.headers.get("Retry-After")) or 0.0,
                _seconds(resp.headers.get("x-ratelimit-reset")) if resp.status_code == 429 else 0.0,
            )
        base = min(max(wait_for, min(2**attempt, 30)), MAX_BACKOFF)
        time.sleep(base + random.uniform(0, 0.5))


def _seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value.split(",")[0].strip()))
    except ValueError:
        return 0.0


def parse_rows(raw: bytes, fmt: str = "JSON") -> list[dict[str, Any]]:
    """Turn a downloaded JSON, JSON-lines or CSV file into a list of row dicts."""
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return []
    if fmt.upper() == "CSV" or (not text.startswith(("{", "[")) and "," in text.splitlines()[0]):
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
    try:
        data = json.loads(text)
    except ValueError:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)
                rows.append(obj if isinstance(obj, dict) else {"value": obj})
        return rows
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        # Async reports group rows by entity ID: {"<id>": [{...}, ...], ...}.
        if data and all(isinstance(v, list) for v in data.values()):
            rows = []
            for key, group in data.items():
                for r in group:
                    row = dict(r) if isinstance(r, dict) else {"value": r}
                    row.setdefault("_group", key)
                    rows.append(row)
            return rows
        return [data]
    return [{"value": data}]
