"""Bulk changes: pause or activate many entities, adjust bids and budgets by a percentage,
Pinterest's bulk upsert, and bulk export of ad entities."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import PinterestAdsClient, parse_rows, segment
from ._common import check_ids, fetch_by_ids, minimal_before, send_batches
from ._progress import report, run_blocking
from .ad_groups import AD_GROUPS
from .ads import ADS
from .campaigns import CAMPAIGNS, validate_campaigns

STATE_ENTITIES = {"campaigns": CAMPAIGNS, "ad_groups": AD_GROUPS, "ads": ADS}
BUDGET_FIELDS = {
    ("campaigns", "daily"): "daily_spend_cap",
    ("campaigns", "lifetime"): "lifetime_spend_cap",
    ("ad_groups", "daily"): "budget_in_micro_currency",
    ("ad_groups", "lifetime"): "budget_in_micro_currency",
}
EXPORT_TYPES = {"CAMPAIGN", "AD_GROUP", "PRODUCT_GROUP", "AD", "KEYWORD", "LABEL", "SCHEDULE"}
UPSERT_KEYS = {
    "campaigns",
    "ad_groups",
    "ads",
    "product_groups",
    "keywords",
    "catalog_product_groups",
    "labels",
    "schedules",
}
MAX_IDS = 5000


def _count(section: dict[str, Any] | None) -> int:
    total = 0
    for value in (section or {}).values():
        if isinstance(value, list):
            for v in value:
                kws = v.get("keywords") if isinstance(v, dict) else None
                total += len(kws) if isinstance(kws, list) else 1
    return total


def _new_amount(
    old: int | None,
    change_pct: float | None,
    set_amount: float | None,
    floor: float | None,
    ceiling: float | None,
) -> int | None:
    if set_amount is not None:
        new = safety.to_micro(set_amount)
    elif old is None:
        return None
    else:
        new = round(old * (1 + float(change_pct or 0) / 100))
    if floor is not None:
        new = max(safety.to_micro(floor), new)
    if ceiling is not None:
        new = min(safety.to_micro(ceiling), new)
    # Pinterest stores whole cents; round to 10,000 micro-units.
    return int(round(new / 10_000) * 10_000)


def register(mcp, client: PinterestAdsClient) -> None:
    async def _adjust(
        spec_name: str,
        field: str,
        kind: str,
        ids: list[str],
        change_pct: float | None,
        set_amount: float | None,
        floor: float | None,
        ceiling: float | None,
        ad_account_id: str | None,
        confirm: str,
        ctx: Context | None,
    ) -> dict[str, Any]:
        if (change_pct is None) == (set_amount is None):
            raise ValueError("Pass exactly one of change_pct or the set_ value.")
        id_list = check_ids(ids, "ids", MAX_IDS)
        if not id_list:
            raise ValueError("Pass at least one ID.")
        spec = STATE_ENTITIES[spec_name]
        aid = client.resolve_ad_account(ad_account_id)
        current = await run_blocking(fetch_by_ids, client, spec, aid, id_list)
        found = {str(c.get("id")): c for c in current if isinstance(c, dict)}
        missing = [i for i in id_list if i not in found]
        items: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for eid in id_list:
            row = found.get(eid)
            if row is None:
                continue
            old = row.get(field)
            old_int = int(old) if isinstance(old, int | float) else None
            new = _new_amount(old_int, change_pct, set_amount, floor, ceiling)
            if new is None:
                skipped.append({"id": eid, "reason": f"no {field} set (automatic or unset)"})
                continue
            if old_int is not None and old_int == new:
                skipped.append({"id": eid, "reason": "unchanged"})
                continue
            items.append({"id": eid, field: new})
        summary = {
            "entity": spec_name,
            "field": field,
            "to_change": len(items),
            "not_found": missing[:50],
            "skipped": skipped[:50],
        }
        if not items:
            return {**summary, "message": "Nothing to change."}
        changes = safety.money_changes(items, current, fields=((kind, field),))
        decision = safety.assess(client.config, count=len(items), changes=changes)
        refused = safety.refusal(client.config, decision, confirm)
        if refused:
            return {**refused, **summary}
        before = minimal_before(current, items)
        sent = await send_batches(
            client,
            "PATCH",
            client.account_path(aid, spec.path),
            items,
            op=f"adjust_{kind}_{spec_name}",
            ad_account_id=aid,
            before_by_id={str(b["id"]): b for b in before},
            ctx=ctx,
        )
        return safety.annotate({**summary, **sent}, decision)

    @mcp.tool()
    async def pinterest_ads_set_statuses(
        entity: str,
        ids: list[str],
        status: str,
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Pause or activate many campaigns, ad groups or ads at once, with progress.

        entity: 'campaigns', 'ad_groups' or 'ads'. status: 'PAUSED' or 'ACTIVE' (to archive,
        use the matching archive tool). ACTIVE starts spending as soon as the parents are
        active too. More than PINTEREST_ADS_BULK_THRESHOLD items need confirm='APPLY'.
        Current statuses are kept in the audit log.
        """
        if entity not in STATE_ENTITIES:
            raise ValueError(f"entity must be one of {', '.join(STATE_ENTITIES)}.")
        state = status.strip().upper()
        if state not in {"PAUSED", "ACTIVE"}:
            raise ValueError("status must be PAUSED or ACTIVE.")
        id_list = check_ids(ids, "ids", MAX_IDS)
        if not id_list:
            raise ValueError("Pass at least one ID.")
        spec = STATE_ENTITIES[entity]
        aid = client.resolve_ad_account(ad_account_id)
        items = [{"id": i, "status": state} for i in id_list]
        decision = safety.assess(client.config, count=len(items))
        refused = safety.refusal(client.config, decision, confirm)
        if refused:
            return {**refused, "entity": entity, "status": state, "count": len(items)}
        current = await run_blocking(client.snapshot, fetch_by_ids, client, spec, aid, id_list)
        before = minimal_before(current, items)
        sent = await send_batches(
            client,
            "PATCH",
            client.account_path(aid, spec.path),
            items,
            op=f"set_status_{entity}",
            ad_account_id=aid,
            before_by_id={str(b["id"]): b for b in before},
            ctx=ctx,
        )
        return safety.annotate({"entity": entity, "status": state, **sent}, decision)

    @mcp.tool()
    async def pinterest_ads_adjust_ad_group_bids(
        ids: list[str],
        change_pct: float | None = None,
        set_bid: float | None = None,
        min_bid: float | None = 0.05,
        max_bid: float | None = None,
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change many ad group bids at once, with progress.

        Pass change_pct (e.g. -10 lowers each bid by 10%) or set_bid (one value for all, in
        the account currency, e.g. 0.85). New bids stay between min_bid and max_bid
        (currency units). Ad groups on automatic bidding have no bid and are skipped. Current
        bids are read first; the result lists old and new values.

        Needs confirm='APPLY' for more than PINTEREST_ADS_BULK_THRESHOLD ad groups or any rise
        above PINTEREST_ADS_MAX_INCREASE_PCT. Run in dry-run first and show the user the table.
        """
        return await _adjust(
            "ad_groups",
            "bid_in_micro_currency",
            "bid",
            ids,
            change_pct,
            set_bid,
            min_bid,
            max_bid,
            ad_account_id,
            confirm,
            ctx,
        )

    @mcp.tool()
    async def pinterest_ads_adjust_budgets(
        entity: str,
        ids: list[str],
        change_pct: float | None = None,
        set_budget: float | None = None,
        budget: str = "daily",
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change many campaign or ad group budgets at once, with progress.

        entity: 'campaigns' (budget 'daily' -> daily_spend_cap, 'lifetime' ->
        lifetime_spend_cap) or 'ad_groups' (budget_in_micro_currency). Pass change_pct
        (e.g. 15 raises by 15%) or set_budget in the account currency (e.g. 40). Pinterest
        rejects daily rises smaller than 1.00 USD (or local equivalent).

        Needs confirm='APPLY' for more than PINTEREST_ADS_BULK_THRESHOLD items, any rise above
        PINTEREST_ADS_MAX_INCREASE_PCT, or a value over PINTEREST_ADS_MAX_BUDGET.
        """
        key = (entity, budget.strip().lower())
        if key not in BUDGET_FIELDS:
            raise ValueError(
                "entity must be 'campaigns' or 'ad_groups' and budget 'daily' or 'lifetime'."
            )
        return await _adjust(
            entity,
            BUDGET_FIELDS[key],
            "budget",
            ids,
            change_pct,
            set_budget,
            None,
            None,
            ad_account_id,
            confirm,
            ctx,
        )

    @mcp.tool()
    def pinterest_ads_bulk_upsert(
        create: dict[str, Any] | None = None,
        update: dict[str, Any] | None = None,
        ad_account_id: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create and/or update many ad entities in one asynchronous Pinterest bulk job.

        create / update: objects with lists under campaigns, ad_groups, ads, product_groups
        (product group promotions: {"ad_group_id", "product_group_promotion"}), keywords
        ({"parent_id", "keywords"}), catalog_product_groups, labels or schedules. Each entry
        uses the same fields as the matching create or update tool (updates need id).
        Campaign create entries without a status are sent PAUSED.

        Pinterest applies the job in the background and this server can't check every
        change, so it always needs confirm='APPLY' (confirm='ARCHIVE' if anything is set to
        ARCHIVED). Returns a request_id to follow with pinterest_ads_get_bulk_request.
        """
        if not create and not update:
            raise ValueError("Pass create and/or update.")
        body: dict[str, Any] = {}
        archives = 0
        for name, section in (("create", create), ("update", update)):
            if not section:
                continue
            unknown = sorted(set(section) - UPSERT_KEYS)
            if unknown:
                raise ValueError(f"{name} has unknown keys {unknown}.")
            section = {k: list(v) for k, v in section.items()}
            if name == "create":
                campaigns = [dict(c) for c in section.get("campaigns", [])]
                for c in campaigns:
                    c.setdefault("status", "PAUSED")
                if campaigns:
                    validate_campaigns(campaigns, True)
                    section["campaigns"] = campaigns
            elif section.get("campaigns"):
                validate_campaigns(section["campaigns"], False)
            for value in section.values():
                archives += safety.archives_in(v for v in value if isinstance(v, dict))
            body[name] = section
        count = _count(body.get("create")) + _count(body.get("update"))
        decision = safety.assess(
            client.config,
            count=count,
            destructive=safety.ARCHIVE if archives else None,
            always=safety.APPLY,
            reason="bulk upsert job; its effect isn't checked item by item",
        )
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        aid = client.resolve_ad_account(ad_account_id)
        out = client.request(
            "POST",
            client.account_path(aid, "bulk", "upsert"),
            json_body=body,
            ad_account_id=aid,
            op="bulk_upsert",
        )
        return safety.annotate(out, decision)

    def bulk_status(request_id: str, aid: str, include_details: bool) -> dict[str, Any]:
        out = client.request(
            "GET",
            client.account_path(aid, "bulk", segment(request_id, "request_id")),
            params={"include_details": str(include_details).lower()},
        )
        return out if isinstance(out, dict) else {"result": out}

    def bulk_rows(url: str, fmt: str) -> list[dict[str, Any]]:
        return parse_rows(client.download(url), fmt)

    @mcp.tool()
    def pinterest_ads_get_bulk_request(
        request_id: str,
        include_details: bool = False,
        download: bool = False,
        max_rows: int = 1000,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Status of a bulk upsert or bulk export: RUNNING, SUCCEEDED or FAILED.

        With download=true and status SUCCEEDED, fetches the result file and returns its rows
        (up to max_rows), held in memory only.
        """
        aid = client.resolve_ad_account(ad_account_id)
        st = bulk_status(request_id, aid, include_details)
        url = st.pop("result_url", None)
        if download and st.get("status") == "SUCCEEDED" and url:
            rows = bulk_rows(str(url), "JSON")
            return {
                **st,
                "row_count": len(rows),
                "rows": rows[: max(0, max_rows)],
                "truncated": len(rows) > max_rows,
            }
        return {**st, "result_ready": bool(url)}

    @mcp.tool()
    async def pinterest_ads_bulk_export(
        entity_types: list[str],
        entity_ids: list[str] | None = None,
        campaign_filter: dict[str, Any] | None = None,
        updated_since: str | None = None,
        max_rows: int = 2000,
        timeout_seconds: float = 600,
        poll_seconds: float = 10,
        ad_account_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export ad entities in bulk (one background job), wait with progress, return rows.

        entity_types: up to 6 of CAMPAIGN, AD_GROUP, PRODUCT_GROUP, AD, KEYWORD, LABEL,
        SCHEDULE. Pinterest exports active entities only. campaign_filter e.g.
        {"campaign_status": ["RUNNING"], "objective_type": ["SALES"]}. updated_since is a
        timestamp string to export only recent changes. Changes nothing in the account.
        """
        types = [t.strip().upper() for t in entity_types if t.strip()]
        if not types or len(types) > 6 or not set(types) <= EXPORT_TYPES:
            raise ValueError(f"entity_types: 1 to 6 of {', '.join(sorted(EXPORT_TYPES))}.")
        aid = client.resolve_ad_account(ad_account_id)
        body: dict[str, Any] = {"entity_types": types, "output_format": "JSON"}
        if entity_ids:
            body["entity_ids"] = check_ids(entity_ids, "entity_ids", MAX_IDS)
        if campaign_filter:
            body["campaign_filter"] = campaign_filter
        if updated_since:
            body["updated_since"] = updated_since
        created = await run_blocking(
            client.request,
            "POST",
            client.account_path(aid, "bulk", "download"),
            json_body=body,
            read_only=True,
        )
        request_id = created.get("request_id") if isinstance(created, dict) else None
        if not request_id:
            raise RuntimeError(f"No request_id in the response: {created}")
        step = 1
        await report(ctx, step, None, f"Requested export {request_id}")
        deadline = time.monotonic() + timeout_seconds
        while True:
            st = await run_blocking(bulk_status, str(request_id), aid, False)
            state = st.get("status")
            step += 1
            url = st.get("result_url")
            if state == "SUCCEEDED" and url:
                await report(ctx, step, None, "Export ready; downloading")
                rows = await run_blocking(bulk_rows, str(url), "JSON")
                return {
                    "request_id": request_id,
                    "status": state,
                    "row_count": len(rows),
                    "rows": rows[: max(0, max_rows)],
                    "truncated": len(rows) > max_rows,
                }
            if state == "FAILED":
                return {"request_id": request_id, "status": state}
            if time.monotonic() >= deadline:
                return {
                    "request_id": request_id,
                    "status": state,
                    "message": "Still running. Check later with pinterest_ads_get_bulk_request.",
                }
            await report(ctx, step, None, f"Export {state}; waiting")
            await asyncio.sleep(max(0.0, poll_seconds))
