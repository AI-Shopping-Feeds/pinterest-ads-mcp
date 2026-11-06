"""Conversion tags (read only): the IDs SALES and LEADS ad groups optimise towards."""

from __future__ import annotations

from typing import Any

from ..client import PinterestAdsClient, segment


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_list_conversion_tags(
        ad_account_id: str | None = None, include_deleted: bool = False
    ) -> Any:
        """List the ad account's conversion tags (Pinterest Tag), with their IDs and status.

        A SALES or LEADS ad group needs one of these IDs as
        optimization_goal_metadata.conversion_tag_v3_goal_metadata.conversion_tag_id.
        """
        return client.request(
            "GET",
            client.account_path(ad_account_id, "conversion_tags"),
            params={"filter_deleted": str(not include_deleted).lower()},
        )

    @mcp.tool()
    def pinterest_ads_get_conversion_tag(
        conversion_tag_id: str, ad_account_id: str | None = None
    ) -> Any:
        """Get one conversion tag: name, status, last fired time and configuration."""
        return client.request(
            "GET",
            client.account_path(
                ad_account_id, "conversion_tags", segment(conversion_tag_id, "conversion_tag_id")
            ),
        )
