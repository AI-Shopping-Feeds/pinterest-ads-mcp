"""Campaigns: list, create (PAUSED by default), update and archive.

Objective simplification: Pinterest stops API creation of CATALOG_SALES and
WEB_CONVERSION campaigns on 1 February 2027 and archives them on 30 April
2027. These tools refuse those objectives (and the retired VIDEO_VIEW and
WEB_SESSIONS) and point to SALES, LEADS or CONSIDERATION instead.
"""

from __future__ import annotations

from typing import Any

from ..client import PinterestAdsClient
from ._common import Entity, register_entity

LEGACY_OBJECTIVES = {
    "CATALOG_SALES": "use SALES (or CONSIDERATION) with intended_promotion_type 'CATALOG'",
    "WEB_CONVERSION": "use SALES for checkout/add-to-cart goals or LEADS for signup/lead goals",
    "VIDEO_VIEW": "use VIDEO_COMPLETION",
    "WEB_SESSIONS": "use CONSIDERATION",
}


def validate_campaigns(items: list[dict[str, Any]], creating: bool) -> None:
    for i, item in enumerate(items):
        objective = str(item.get("objective_type") or "").upper()
        if objective in LEGACY_OBJECTIVES:
            raise ValueError(
                f"items[{i}]: objective_type {objective} is being retired by Pinterest "
                f"(no new API campaigns from 1 Feb 2027, archived 30 Apr 2027); "
                f"{LEGACY_OBJECTIVES[objective]}."
            )
        if creating and not objective:
            raise ValueError(f"items[{i}] needs objective_type (e.g. AWARENESS, SALES, LEADS).")
        if creating and not item.get("name"):
            raise ValueError(f"items[{i}] needs a name.")
        if str(item.get("intended_promotion_type") or "").upper() == "LEGACY":
            raise ValueError(
                f"items[{i}]: intended_promotion_type LEGACY only applies to retiring "
                "objectives; use STANDARD_AD or CATALOG."
            )


CAMPAIGNS = Entity(
    noun="campaigns",
    label="campaigns",
    path="campaigns",
    id_param="campaign_ids",
    money=(("budget", "daily_spend_cap"), ("budget", "lifetime_spend_cap")),
    create_example=(
        'Example: {"name": "Spring sale", "objective_type": "SALES", '
        '"intended_promotion_type": "STANDARD_AD", "daily_spend_cap": 20000000, '
        '"is_campaign_budget_optimization": true}. objective_type is one of AWARENESS, '
        "CONSIDERATION, VIDEO_COMPLETION, SALES, LEADS, APP_INSTALL; SALES and CONSIDERATION "
        "take intended_promotion_type STANDARD_AD (ads from Pins) or CATALOG (product group "
        "promotions). start_time/end_time are Unix seconds."
    ),
    update_example=(
        'Example: {"id": "626747269410", "daily_spend_cap": 25000000} or '
        '{"id": "...", "status": "PAUSED"}. Pinterest rejects daily budget rises smaller than '
        "1.00 USD (or local equivalent). objective_type can only change on draft campaigns."
    ),
    validate=validate_campaigns,
)


def register(mcp, client: PinterestAdsClient) -> None:
    register_entity(mcp, client, CAMPAIGNS)
