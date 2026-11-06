"""Ads (promoted Pins) and product group promotions (catalog ads)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import PinterestAdsClient
from ._common import (
    MAX_FILTER_IDS,
    PAUSED_NOTE,
    Entity,
    check_ids,
    check_items,
    chunks,
    minimal_before,
    register_entity,
    require_ids,
    send_batches,
    status_filter,
)
from ._progress import run_blocking

ADS = Entity(
    noun="ads",
    label="ads",
    path="ads",
    id_param="ad_ids",
    filters={"campaign_ids": "campaign_ids", "ad_group_ids": "ad_group_ids"},
    create_example=(
        'Example: {"ad_group_id": "2680059592705", "pin_id": "813744226420795884", '
        '"creative_type": "REGULAR", "name": "Red trainers", '
        '"destination_url": "https://example.com/red-trainers"}. The Pin must belong to the '
        "ad account's profile (find one with pinterest_ads_list_pins or make one with "
        "pinterest_ads_create_ad_pin). creative_type follows the Pin: REGULAR (image), "
        "VIDEO, MAX_VIDEO, CAROUSEL, IDEA, COLLECTION, SHOPPING, QUIZ. Ads go through "
        "Pinterest's review before they serve."
    ),
    update_example=(
        'Example: {"id": "687195134316", "status": "PAUSED"} or '
        '{"id": "...", "destination_url": "https://example.com/new"}.'
    ),
)

PGP_PATH = "product_group_promotions"
PGP_MONEY = (("bid", "bid_in_micro_currency"),)


def _fetch_pgps(
    client: PinterestAdsClient,
    ad_account_id: str | None,
    *,
    ids: list[str] | None = None,
    ad_group_id: str | None = None,
    statuses: list[str] | None = None,
    max_results: int = 200,
) -> tuple[list[Any], bool]:
    params: dict[str, Any] = {"entity_statuses": status_filter(statuses, by_id=bool(ids))}
    if ids:
        params["product_group_promotion_ids"] = check_ids(ids, "ids", MAX_FILTER_IDS)
    if ad_group_id:
        params["ad_group_id"] = check_ids([ad_group_id], "ad_group_id")[0]
    return client.paginate(
        client.account_path(ad_account_id, PGP_PATH), params, max_results=max_results
    )


def _pgps_by_ids(client: PinterestAdsClient, aid: str, ids: list[str]) -> list[Any]:
    out: list[Any] = []
    for part in chunks(ids, MAX_FILTER_IDS):
        items, _ = _fetch_pgps(client, aid, ids=list(part), max_results=len(part))
        out.extend(items)
    return out


def _unwrap(items: list[Any]) -> list[dict[str, Any]]:
    """List responses may wrap each promotion as {"data": {...}}."""
    return [i.get("data", i) if isinstance(i, dict) else i for i in items]


def register(mcp, client: PinterestAdsClient) -> None:
    register_entity(mcp, client, ADS)

    @mcp.tool()
    def pinterest_ads_list_product_group_promotions(
        ad_account_id: str | None = None,
        ids: list[str] | None = None,
        ad_group_id: str | None = None,
        statuses: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List product group promotions: the catalog product groups an ad group promotes.

        Filter by ids, ad_group_id and statuses (default ACTIVE and PAUSED; ['ALL'] for
        every status).
        """
        items, truncated = _fetch_pgps(
            client,
            ad_account_id,
            ids=ids,
            ad_group_id=ad_group_id,
            statuses=statuses,
            max_results=max_results,
        )
        return {"product_group_promotions": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    async def pinterest_ads_create_product_group_promotions(
        ad_group_id: str,
        items: list[dict[str, Any]],
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Promote catalog product groups in an ad group (catalog ads). Changes the live account.

        items: e.g. [{"catalog_product_group_id": "1231235", "creative_type": "SHOPPING",
        "bid_in_micro_currency": 1200000}]. Use with SALES or CONSIDERATION campaigns whose
        intended_promotion_type is CATALOG. Items without a status are created PAUSED.
        Values over PINTEREST_ADS_MAX_BID and large batches need confirm='APPLY'.
        """
        check_items(items)
        group = check_ids([ad_group_id], "ad_group_id")[0]
        aid = client.resolve_ad_account(ad_account_id)
        prepared = []
        defaulted = 0
        for item in items:
            row = dict(item)
            if "status" not in row:
                row["status"] = "PAUSED"
                defaulted += 1
            prepared.append(row)
        decision = safety.assess(
            client.config,
            count=len(prepared),
            changes=safety.new_values(prepared, PGP_MONEY),
            creating=True,
        )
        refused = safety.refusal(client.config, decision, confirm, preview=prepared[:50])
        if refused:
            return refused
        out = await send_batches(
            client,
            "POST",
            client.account_path(aid, PGP_PATH),
            prepared,
            op="create_product_group_promotions",
            ad_account_id=aid,
            ctx=ctx,
            wrap=lambda part: {"ad_group_id": group, "product_group_promotion": part},
        )
        if defaulted:
            out["created_paused"] = defaulted
            out["note"] = PAUSED_NOTE.replace("pinterest_ads_set_statuses", "this update tool")
        return safety.annotate(out, decision)

    @mcp.tool()
    async def pinterest_ads_update_product_group_promotions(
        ad_group_id: str,
        items: list[dict[str, Any]],
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Update product group promotions in one ad group. Changes the live account.

        items: objects with id plus the fields to change, e.g. {"id": "...", "status":
        "PAUSED"} or {"id": "...", "bid_in_micro_currency": 900000}. Bid rises above
        PINTEREST_ADS_MAX_INCREASE_PCT and large batches need confirm='APPLY'; status
        ARCHIVED needs confirm='ARCHIVE'.
        """
        check_items(items)
        ids = require_ids(items)
        group = check_ids([ad_group_id], "ad_group_id")[0]
        aid = client.resolve_ad_account(ad_account_id)
        current = await run_blocking(client.snapshot, _pgps_by_ids, client, aid, ids)
        current = _unwrap(current or [])
        decision = safety.assess(
            client.config,
            count=len(items),
            changes=safety.money_changes(items, current, fields=PGP_MONEY),
            destructive=safety.ARCHIVE if safety.archives_in(items) else None,
        )
        refused = safety.refusal(client.config, decision, confirm, preview=items[:50])
        if refused:
            return refused
        before = minimal_before(current, items)
        out = await send_batches(
            client,
            "PATCH",
            client.account_path(aid, PGP_PATH),
            items,
            op="update_product_group_promotions",
            ad_account_id=aid,
            before_by_id={str(b["id"]): b for b in before},
            ctx=ctx,
            wrap=lambda part: {"ad_group_id": group, "product_group_promotion": part},
        )
        return safety.annotate(out, decision)
