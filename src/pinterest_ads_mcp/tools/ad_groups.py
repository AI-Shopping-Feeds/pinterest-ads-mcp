"""Ad groups: list, create (PAUSED by default), update, archive, and bid floors."""

from __future__ import annotations

from typing import Any

from ..client import PinterestAdsClient
from ._common import Entity, register_entity

AD_GROUPS = Entity(
    noun="ad_groups",
    label="ad groups",
    path="ad_groups",
    id_param="ad_group_ids",
    filters={"campaign_ids": "campaign_ids"},
    money=(("budget", "budget_in_micro_currency"), ("bid", "bid_in_micro_currency")),
    create_example=(
        'Example: {"name": "UK women 25-44", "campaign_id": "626747269410", '
        '"billable_event": "CLICKTHROUGH", "bid_strategy_type": "AUTOMATIC_BID", '
        '"budget_in_micro_currency": 10000000, "budget_type": "DAILY", '
        '"targeting_spec": {"LOCATION": ["GB"], "GENDER": ["female"], '
        '"AGE_BUCKET": ["25-34", "35-44"]}}. For SALES or LEADS campaigns set '
        '"optimization_goal_metadata": {"conversion_tag_v3_goal_metadata": '
        '{"conversion_tag_id": "...", "conversion_event": "CHECKOUT"}} '
        "(CHECKOUT or ADD_TO_CART for SALES, SIGNUP or LEAD for LEADS). "
        "targeting_spec needs LOCATION or GEO. Use pinterest_ads_get_targeting_options for "
        "valid codes."
    ),
    update_example=(
        'Example: {"id": "2680059592705", "bid_in_micro_currency": 1500000} or '
        '{"id": "...", "targeting_spec": {...}}. targeting_spec replaces the whole spec.'
    ),
)


def register(mcp, client: PinterestAdsClient) -> None:
    register_entity(mcp, client, AD_GROUPS)

    @mcp.tool()
    def pinterest_ads_get_bid_floors(
        bid_floor_specs: list[dict[str, Any]],
        targeting_spec: dict[str, Any] | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Get the minimum bids Pinterest accepts for ad groups. Changes nothing.

        bid_floor_specs: e.g. [{"billable_event": "CLICKTHROUGH", "objective_type":
        "CONSIDERATION", "creative_type": "REGULAR", "countries": ["GB"], "currency":
        "GBP"}]. targeting_spec is optional, as in ad group create. Values are
        micro-units (1.00 = 1000000).
        """
        if not bid_floor_specs:
            raise ValueError("Pass at least one bid floor spec.")
        body: dict[str, Any] = {"bid_floor_specs": bid_floor_specs}
        if targeting_spec:
            body["targeting_spec"] = targeting_spec
        return client.request(
            "POST",
            client.account_path(ad_account_id, "bid_floor"),
            json_body=body,
            read_only=True,
        )
