"""Shared building blocks for the tool modules.

Campaigns, ad groups and ads follow one pattern in the Pinterest API v5:
GET ``/ad_accounts/{id}/<entity>`` lists (bookmark paging, ID and status
filters, at most 250 IDs per filter), POST creates and PATCH updates arrays of
at most 30 objects, and archiving is a PATCH that sets ``status`` to
``ARCHIVED`` (Pinterest has no delete for these).

`register_entity()` generates list, create, update and archive tools for one
entity from a small spec, with the same guards, before-snapshots, batching and
audit trail for each.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..auth import redact
from ..client import PinterestAdsClient
from ._progress import report, run_blocking

BATCH = 30  # Pinterest's limit for create/update arrays
MAX_ITEMS = 500  # per tool call; larger jobs belong in pinterest_ads_bulk_upsert
MAX_FILTER_IDS = 250  # Pinterest's limit for ID filters on list endpoints
ALL_STATUSES = ("ACTIVE", "PAUSED", "ARCHIVED", "DRAFT")
DEFAULT_STATUSES = ("ACTIVE", "PAUSED")
PAUSED_NOTE = (
    "Created PAUSED (the default here) so nothing spends yet. Check them, then turn them "
    "on with pinterest_ads_set_statuses once the user agrees."
)


def ids_to_strings(ids: Iterable[Any] | None) -> list[str]:
    return [str(i).strip() for i in (ids or []) if str(i).strip()]


def check_ids(ids: Iterable[Any] | None, what: str = "ids", max_n: int = MAX_ITEMS) -> list[str]:
    out = ids_to_strings(ids)
    if len(out) > max_n:
        raise ValueError(f"At most {max_n} {what} per call; got {len(out)}.")
    bad = [i for i in out if not i.isdigit()]
    if bad:
        raise ValueError(f"{what} must be numeric Pinterest IDs; got {bad[:5]}.")
    return out


def check_items(items: Any, what: str = "items", max_items: int = MAX_ITEMS) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise ValueError(f"Pass at least one object in {what}.")
    if len(items) > max_items:
        raise ValueError(f"At most {max_items} {what} per call; got {len(items)}.")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{what}[{i}] must be an object.")
    return items


def require_ids(items: list[dict[str, Any]], id_field: str = "id") -> list[str]:
    missing = [i for i, it in enumerate(items) if not it.get(id_field)]
    if missing:
        raise ValueError(f"Every item needs '{id_field}'; missing at index {missing[:10]}.")
    return check_ids([it[id_field] for it in items], id_field)


def status_filter(statuses: list[str] | None, *, by_id: bool) -> list[str]:
    """Normalise a status filter. Default ACTIVE+PAUSED; ['ALL'] or an ID lookup -> every status."""
    if not statuses:
        return list(ALL_STATUSES if by_id else DEFAULT_STATUSES)
    chosen = [s.strip().upper() for s in statuses if s.strip()]
    if "ALL" in chosen:
        return list(ALL_STATUSES)
    bad = [s for s in chosen if s not in ALL_STATUSES]
    if bad:
        raise ValueError(f"Unknown status {bad}; use ACTIVE, PAUSED, ARCHIVED, DRAFT or ALL.")
    return chosen


def chunks(values: Sequence[Any], size: int) -> list[Sequence[Any]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def minimal_before(
    current: Iterable[dict[str, Any]] | None,
    items: Iterable[dict[str, Any]],
    *,
    id_field: str = "id",
    always: Sequence[str] = ("status",),
) -> list[dict[str, Any]]:
    """Old values of only the fields each update changes (plus status), for the audit log."""
    by_id = {str(c.get(id_field)): c for c in (current or []) if isinstance(c, dict)}
    out: list[dict[str, Any]] = []
    for item in items:
        eid = str(item.get(id_field))
        cur = by_id.get(eid)
        if cur is None:
            continue
        snap: dict[str, Any] = {id_field: cur.get(id_field)}
        for key in [*item.keys(), *always]:
            if key != id_field and key in cur:
                snap[key] = cur[key]
        out.append(snap)
    return out


def merge_results(results: list[Any], failed: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine the responses of several batched writes into one result."""
    items: list[Any] = []
    audit_ids: list[str] = []
    rehearsed: list[Any] = []
    for r in results:
        if isinstance(r, dict) and r.get("_dry_run"):
            rehearsed.append(r)
            continue
        if isinstance(r, dict):
            batch = r.get("items")
            if isinstance(batch, list):
                items.extend(batch)
            if r.get("audit_id"):
                audit_ids.append(r["audit_id"])
    out: dict[str, Any] = {}
    if rehearsed:
        out["dry_run"] = True
        out["would_send"] = rehearsed
    else:
        with_errors = [i for i in items if isinstance(i, dict) and i.get("exceptions")]
        out["items"] = items
        out["succeeded"] = len(items) - len(with_errors)
        out["with_errors"] = len(with_errors)
    out["audit_ids"] = audit_ids
    if failed:
        out["failed_batches"] = failed
    return out


async def send_batches(
    client: PinterestAdsClient,
    method: str,
    path: str,
    items: list[dict[str, Any]],
    *,
    op: str,
    ad_account_id: str,
    before_by_id: dict[str, Any] | None = None,
    ctx: Context | None = None,
    size: int = BATCH,
    wrap: Callable[[list[dict[str, Any]]], Any] | None = None,
    id_field: str = "id",
) -> dict[str, Any]:
    """Send items in batches of `size`, with progress, one audit entry per batch."""
    results: list[Any] = []
    failed: list[dict[str, Any]] = []
    total = len(items)
    done = 0
    snapshots = before_by_id or {}
    for start in range(0, total, size):
        part = items[start : start + size]
        snap = [snapshots[str(i.get(id_field))] for i in part if str(i.get(id_field)) in snapshots]
        try:
            out = await run_blocking(
                client.request,
                method,
                path,
                json_body=wrap(part) if wrap else part,
                ad_account_id=ad_account_id,
                op=op,
                before=snap or None,
            )
            results.append(out)
        except Exception as e:
            if total <= size:
                raise
            failed.append({"from_index": start, "count": len(part), "reason": redact(str(e))[:300]})
        done += len(part)
        await report(ctx, done, total, f"Sent {done} of {total}")
    return merge_results(results, failed)


@dataclass(frozen=True)
class Entity:
    """One entity under /ad_accounts/{id}/ (campaigns, ad groups, ads)."""

    noun: str  # plural used in tool names, e.g. "ad_groups"
    label: str  # human label, e.g. "ad groups"
    path: str  # path segment under the ad account, e.g. "ad_groups"
    id_param: str  # list filter for this entity's IDs, e.g. "ad_group_ids"
    filters: dict[str, str] = field(default_factory=dict)  # tool arg -> query param
    money: tuple[tuple[str, str], ...] = ()  # (kind, field) pairs in micro-units
    default_status: str | None = "PAUSED"
    create_example: str = ""
    update_example: str = ""
    validate: Callable[[list[dict[str, Any]], bool], None] | None = None


def fetch(
    client: PinterestAdsClient,
    spec: Entity,
    *,
    ad_account_id: str | None,
    ids: list[str] | None = None,
    filters: dict[str, list[str] | None] | None = None,
    statuses: list[str] | None = None,
    max_results: int = 200,
) -> tuple[list[Any], bool]:
    params: dict[str, Any] = {}
    for arg, values in (filters or {}).items():
        if not values:
            continue
        key = spec.filters.get(arg)
        if key is None:
            supported = ", ".join(sorted(spec.filters)) or "none"
            raise ValueError(f"{spec.label} can't be filtered by {arg} (supported: {supported}).")
        params[key] = check_ids(values, arg, MAX_FILTER_IDS)
    id_list = check_ids(ids, "ids", MAX_FILTER_IDS) if ids else []
    if id_list:
        params[spec.id_param] = id_list
    params["entity_statuses"] = status_filter(statuses, by_id=bool(id_list))
    return client.paginate(
        client.account_path(ad_account_id, spec.path), params, max_results=max_results
    )


def fetch_by_ids(
    client: PinterestAdsClient, spec: Entity, ad_account_id: str, ids: list[str]
) -> list[Any]:
    """Current state of up to MAX_ITEMS entities, 250 IDs per request."""
    out: list[Any] = []
    for part in chunks(ids, MAX_FILTER_IDS):
        items, _ = fetch(
            client, spec, ad_account_id=ad_account_id, ids=list(part), max_results=len(part)
        )
        out.extend(items)
    return out


def register_entity(mcp, client: PinterestAdsClient, spec: Entity) -> None:
    filter_args = ", ".join(sorted(spec.filters)) or "none"
    money_fields = ", ".join(name for _, name in spec.money)

    @mcp.tool(
        name=f"pinterest_ads_list_{spec.noun}",
        description=(
            f"List {spec.label} in an ad account.\n\n"
            f"Filters: ids (this entity's IDs, up to {MAX_FILTER_IDS}) plus {filter_args} where "
            "supported. statuses defaults to ACTIVE and PAUSED; pass ['ARCHIVED'] or ['ALL'] "
            "to widen it (IDs are found in any status). Returns at most max_results items. "
            "summary_status shows whether each one is actually delivering."
        ),
    )
    def list_tool(
        ad_account_id: str | None = None,
        ids: list[str] | None = None,
        campaign_ids: list[str] | None = None,
        ad_group_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        items, truncated = fetch(
            client,
            spec,
            ad_account_id=ad_account_id,
            ids=ids,
            filters={"campaign_ids": campaign_ids, "ad_group_ids": ad_group_ids},
            statuses=statuses,
            max_results=max_results,
        )
        return {spec.noun: items, "count": len(items), "truncated": truncated}

    money_note = (
        f" Money fields ({money_fields}) are micro-units of the account currency (1.00 = 1000000)."
        if spec.money
        else ""
    )

    @mcp.tool(
        name=f"pinterest_ads_create_{spec.noun}",
        description=(
            f"Create {spec.label} (up to {MAX_ITEMS} per call, sent {BATCH} at a time). "
            "Changes the live account.\n\n"
            f"items: one object per {spec.label[:-1]}, sent as-is. {spec.create_example}"
            f"{money_note}\n"
            "Items without a status are created PAUSED. Large batches and values over "
            "PINTEREST_ADS_MAX_BUDGET/PINTEREST_ADS_MAX_BID need confirm='APPLY'. Rehearse "
            "with dry-run first."
        ),
    )
    async def create_tool(
        items: list[dict[str, Any]],
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        check_items(items)
        if spec.validate:
            spec.validate(items, True)
        aid = client.resolve_ad_account(ad_account_id)
        prepared: list[dict[str, Any]] = []
        defaulted = 0
        for item in items:
            row = dict(item)
            if spec.default_status and "status" not in row:
                row["status"] = spec.default_status
                defaulted += 1
            prepared.append(row)
        decision = safety.assess(
            client.config,
            count=len(prepared),
            changes=safety.new_values(prepared, spec.money),
            creating=True,
        )
        refused = safety.refusal(client.config, decision, confirm, preview=prepared[:50])
        if refused:
            return refused
        out = await send_batches(
            client,
            "POST",
            client.account_path(aid, spec.path),
            prepared,
            op=f"create_{spec.noun}",
            ad_account_id=aid,
            ctx=ctx,
        )
        if defaulted:
            out["created_paused"] = defaulted
            out["note"] = PAUSED_NOTE
        return safety.annotate(out, decision)

    @mcp.tool(
        name=f"pinterest_ads_update_{spec.noun}",
        description=(
            f"Update {spec.label} (up to {MAX_ITEMS} per call). Changes the live account.\n\n"
            f"items: objects with id plus only the fields to change. {spec.update_example}"
            f"{money_note}\n"
            "Current values are read first and their old values kept in the audit log. "
            "Bid or budget rises above PINTEREST_ADS_MAX_INCREASE_PCT, and large batches, "
            "need confirm='APPLY'; setting status ARCHIVED needs confirm='ARCHIVE'."
        ),
    )
    async def update_tool(
        items: list[dict[str, Any]],
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        check_items(items)
        ids = require_ids(items)
        if spec.validate:
            spec.validate(items, False)
        aid = client.resolve_ad_account(ad_account_id)
        current = await run_blocking(client.snapshot, fetch_by_ids, client, spec, aid, ids)
        decision = safety.assess(
            client.config,
            count=len(items),
            changes=safety.money_changes(items, current, fields=spec.money),
            destructive=safety.ARCHIVE if safety.archives_in(items) else None,
        )
        refused = safety.refusal(client.config, decision, confirm, preview=items[:50])
        if refused:
            return refused
        before = minimal_before(current, items)
        out = await send_batches(
            client,
            "PATCH",
            client.account_path(aid, spec.path),
            items,
            op=f"update_{spec.noun}",
            ad_account_id=aid,
            before_by_id={str(b["id"]): b for b in before},
            ctx=ctx,
        )
        return safety.annotate(out, decision)

    @mcp.tool(
        name=f"pinterest_ads_archive_{spec.noun}",
        description=(
            f"Archive {spec.label} by ID. Archived {spec.label} stop serving and Pinterest "
            "can't un-archive them, so this needs confirm='ARCHIVE' after the user agrees. "
            "To stop them temporarily, use pinterest_ads_set_statuses with PAUSED instead."
        ),
    )
    async def archive_tool(
        ids: list[str],
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        id_list = check_ids(ids)
        if not id_list:
            raise ValueError("Pass at least one ID.")
        aid = client.resolve_ad_account(ad_account_id)
        decision = safety.assess(client.config, count=len(id_list), destructive=safety.ARCHIVE)
        items = [{"id": i, "status": "ARCHIVED"} for i in id_list]
        refused = safety.refusal(client.config, decision, confirm, preview={"ids": id_list})
        if refused:
            return refused
        current = await run_blocking(client.snapshot, fetch_by_ids, client, spec, aid, id_list)
        before = minimal_before(current, items, always=("status", "name"))
        out = await send_batches(
            client,
            "PATCH",
            client.account_path(aid, spec.path),
            items,
            op=f"archive_{spec.noun}",
            ad_account_id=aid,
            before_by_id={str(b["id"]): b for b in before},
            ctx=ctx,
        )
        return safety.annotate(out, decision)
