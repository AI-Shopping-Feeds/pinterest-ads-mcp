"""Ad accounts, the signed-in user, and business access (read only)."""

from __future__ import annotations

from typing import Any

from ..client import PinterestAdsClient, segment

ASSET_TYPES = {"AD_ACCOUNT", "PROFILE", "ASSET_GROUP", "CATALOG"}


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_list_ad_accounts(
        include_shared_accounts: bool = True, max_results: int = 200
    ) -> dict[str, Any]:
        """List the ad accounts this token can use: owned ones and, by default, ones shared
        through business access (agency and partner access). Each has id, name, currency,
        country, owner and the user's permissions on it."""
        items, truncated = client.paginate(
            "/ad_accounts",
            {"include_shared_accounts": str(include_shared_accounts).lower()},
            max_results=max_results,
        )
        return {"ad_accounts": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_get_ad_account(ad_account_id: str | None = None) -> dict[str, Any]:
        """Get one ad account: name, currency, country, time zone, owner and permissions."""
        return client.request("GET", client.account_path(ad_account_id))

    @mcp.tool()
    def pinterest_ads_get_user_account(ad_account_id: str | None = None) -> dict[str, Any]:
        """Get the Pinterest account behind the token (username, account type, business name).

        With ad_account_id, returns the account that owns that ad account instead.
        """
        params = {"ad_account_id": segment(ad_account_id, "ad_account_id")} if ad_account_id else {}
        return client.request("GET", "/user_account", params=params)

    @mcp.tool()
    def pinterest_ads_list_businesses(max_results: int = 100) -> dict[str, Any]:
        """List the businesses the signed-in user belongs to (Business Manager), with their
        IDs and a summary of their assets. Needs the biz_access:read scope."""
        items, truncated = client.paginate(
            "/businesses/employers", {"assets_summary": "true"}, max_results=max_results
        )
        return {"businesses": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_list_business_assets(
        business_id: str,
        asset_type: str | None = "AD_ACCOUNT",
        permissions: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List a business's assets: ad accounts (default), profiles, catalogs or asset groups,
        with the permissions the user holds on each. Needs the biz_access:read scope.

        permissions filters by role, e.g. ['ADMIN', 'CAMPAIGN_MANAGER', 'ANALYST'].
        """
        params: dict[str, Any] = {}
        if asset_type:
            kind = asset_type.upper()
            if kind not in ASSET_TYPES:
                raise ValueError(f"asset_type must be one of {', '.join(sorted(ASSET_TYPES))}.")
            params["asset_type"] = kind
        if permissions:
            params["permissions"] = [p.upper() for p in permissions]
        items, truncated = client.paginate(
            f"/businesses/{segment(business_id, 'business_id')}/assets",
            params,
            max_results=max_results,
        )
        return {"assets": items, "count": len(items), "truncated": truncated}
