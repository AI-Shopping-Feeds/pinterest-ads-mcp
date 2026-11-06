"""Just enough Pins and boards to build ads: find a Pin to promote, or make an ad-only Pin.

Organic Pin and board management (publishing, editing, deleting, following) is
deliberately left out.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..client import PinterestAdsClient, segment

PIN_FILTERS = {"exclude_native", "exclude_repins", "has_been_promoted"}
DENIED_NOTE = (
    " Apps registered on or after 14 September 2026 may get HTTP 403 "
    "PINNER_DATA_ACCESS_DENIED here until Pinterest approves Pinner data access; don't retry."
)


def _account(ad_account_id: str | None) -> dict[str, Any]:
    return {"ad_account_id": segment(ad_account_id, "ad_account_id")} if ad_account_id else {}


def _https(url: str, name: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an https:// URL.")
    return url


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_list_pins(
        ad_account_id: str | None = None,
        pin_filter: str | None = None,
        creative_types: list[str] | None = None,
        include_protected_pins: bool = False,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List Pins you could promote. Pass ad_account_id to list the Pins of the profile that
        owns that ad account (needed when it isn't your own).

        pin_filter: 'has_been_promoted', 'exclude_repins' or 'exclude_native'.
        include_protected_pins=true adds ad-only Pins. creative_types e.g. ['REGULAR', 'VIDEO'].
        """
        params: dict[str, Any] = _account(ad_account_id)
        if pin_filter:
            if pin_filter not in PIN_FILTERS:
                raise ValueError(f"pin_filter must be one of {', '.join(sorted(PIN_FILTERS))}.")
            params["pin_filter"] = pin_filter
        if creative_types:
            params["creative_types"] = [c.upper() for c in creative_types]
        if include_protected_pins:
            params["include_protected_pins"] = "true"
        items, truncated = client.paginate("/pins", params, max_results=max_results)
        return {"pins": items, "count": len(items), "truncated": truncated}

    @mcp.tool(
        description=(
            "Get one Pin: title, link, media, board and whether it has been promoted. Pass "
            "ad_account_id when the Pin belongs to that ad account's profile." + DENIED_NOTE
        )
    )
    def pinterest_ads_get_pin(pin_id: str, ad_account_id: str | None = None) -> dict[str, Any]:
        return client.request(
            "GET", f"/pins/{segment(pin_id, 'pin_id')}", params=_account(ad_account_id)
        )

    @mcp.tool()
    def pinterest_ads_list_boards(
        ad_account_id: str | None = None, privacy: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        """List boards (yours, or the ad account owner's with ad_account_id), to find Pins.

        privacy: ALL, PUBLIC, PROTECTED (ad-only), SECRET or PUBLIC_AND_SECRET.
        """
        params = _account(ad_account_id)
        if privacy:
            params["privacy"] = privacy.upper()
        items, truncated = client.paginate("/boards", params, max_results=max_results)
        return {"boards": items, "count": len(items), "truncated": truncated}

    @mcp.tool(
        description=(
            "List the Pins on a board, e.g. the protected 'Ad-only Pins' board, to pick one "
            "for an ad." + DENIED_NOTE
        )
    )
    def pinterest_ads_list_board_pins(
        board_id: str,
        ad_account_id: str | None = None,
        creative_types: list[str] | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        params = _account(ad_account_id)
        if creative_types:
            params["creative_types"] = [c.upper() for c in creative_types]
        items, truncated = client.paginate(
            f"/boards/{segment(board_id, 'board_id')}/pins", params, max_results=max_results
        )
        return {"pins": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_create_ad_pin(
        image_url: str,
        link: str,
        title: str | None = None,
        description: str | None = None,
        alt_text: str | None = None,
        ad_account_id: str | None = None,
        board_id: str | None = None,
        ai_disclosures: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an ad-only Pin from an image URL, to use as an ad's pin_id.

        Ad-only Pins sit on a protected 'Ad-only Pins' board: they can't be saved by others or
        shown organically. Pass ad_account_id so the Pin belongs to that ad account's profile.
        ai_disclosures: ['AI_MODIFIED'] and/or ['SYNTHETIC_PERFORMER'] when the image was made
        or changed with AI. Needs the pins:write scope (run `pinterest-ads-mcp auth --scopes`
        with it). Trial-access apps create Pins only their creator can see.
        """
        body: dict[str, Any] = {
            "link": _https(link, "link"),
            "media_source": {
                "source_type": "image_url",
                "url": _https(image_url, "image_url"),
                "is_standard": True,
            },
            "is_removable": True,
        }
        for key, value in (("title", title), ("description", description), ("alt_text", alt_text)):
            if value:
                body[key] = value
        if board_id:
            body["board_id"] = segment(board_id, "board_id")
        if ai_disclosures:
            allowed = {"AI_MODIFIED", "SYNTHETIC_PERFORMER"}
            values = [v.upper() for v in ai_disclosures]
            if not set(values) <= allowed:
                raise ValueError("ai_disclosures may contain AI_MODIFIED and SYNTHETIC_PERFORMER.")
            body["ai_disclosures"] = {"values": values}
        return client.request(
            "POST",
            "/pins",
            params=_account(ad_account_id),
            json_body=body,
            ad_account_id=ad_account_id,
            op="create_ad_pin",
        )
