"""Escape hatch: call any Pinterest API v5 endpoint on the configured host.

Paths are checked before sending: relative to the v5 base, no '..', '//', query
strings or fragments, and no OAuth endpoints. The host is fixed, so these tools
can't reach any other server.
"""

from __future__ import annotations

import re
from typing import Any

from .. import safety
from ..client import PinterestAdsClient, check_path

# POST endpoints that only read: report and export requests, estimates and lookups.
_READ_POST = re.compile(
    r"(/reports$|/reports/brand_category_sku$|/templates/[^/]+/reports$|/mmm_reports$"
    r"|/bulk/download$|/audience_sizing$|/bid_floor$|/delivery_estimates$"
    r"|^/catalogs/items$|^/catalogs/reports$|/get_by_product_group_filters$"
    r"|/local_inventory_items/query$|^/advanced_auction/items/get$)"
)


def _classify(method: str, path: str) -> tuple[str, str, bool]:
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("method must be GET, POST, PUT, PATCH or DELETE.")
    clean = check_path(path)
    is_read = method == "GET" or (method == "POST" and bool(_READ_POST.search(clean)))
    return method, clean, is_read


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_read_request(
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        """Read from any Pinterest API v5 endpoint the other tools don't cover.

        Only GET, and POSTs that just read (report and export requests, audience sizing, bid
        floors, delivery estimates, catalog item lookups), are allowed; use
        pinterest_ads_request for writes. path is relative to the v5 base, e.g.
        '/ad_accounts/123/ads/targeting_analytics'. List values in query are sent as
        repeated parameters; join them with commas yourself where the API wants that.
        """
        method, clean, is_read = _classify(method, path)
        if not is_read:
            raise ValueError(
                f"{method} {clean} may change the account; use pinterest_ads_request instead."
            )
        return client.request(method, clean, params=query, json_body=body, read_only=True)

    @mcp.tool()
    def pinterest_ads_request(
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        confirm: str = "",
    ) -> Any:
        """Call any Pinterest API v5 endpoint the other tools don't cover (e.g. lead forms,
        ad previews, labels, schedules, conversion events, asynchronous customer list uploads).

        path is relative to the v5 base and starts with '/', e.g. '/ad_accounts/123/labels'.
        GET and read-only POSTs are reads. Every other call is a write: it is audited, follows
        dry-run, and needs confirm='APPLY' because this tool can't check what it changes.
        OAuth endpoints are blocked.
        """
        method, clean, is_read = _classify(method, path)
        if not is_read:
            decision = safety.assess(
                client.config,
                count=1,
                always=safety.APPLY,
                reason="raw write through pinterest_ads_request; its effect isn't checked",
            )
            refused = safety.refusal(
                client.config,
                decision,
                confirm,
                preview={"method": method, "path": clean, "query": query, "body": body},
            )
            if refused:
                return refused
        return client.request(
            method,
            clean,
            params=query,
            json_body=body,
            read_only=is_read,
            op=f"request {method} {clean}",
        )
