"""Catalogs, feeds, items and product groups (read, plus basic writes).

Every tool takes an optional ad_account_id: pass it to work on catalogs owned
by the business behind that ad account (shared through business access).
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import PinterestAdsClient, segment

ITEM_OPERATIONS = {"CREATE", "UPDATE", "UPSERT", "DELETE"}
CATALOG_TYPES = {"RETAIL", "HOTEL", "CREATIVE_ASSETS"}
MAX_BATCH_ITEMS = 1000


def _account(ad_account_id: str | None) -> dict[str, Any]:
    return {"ad_account_id": segment(ad_account_id, "ad_account_id")} if ad_account_id else {}


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_list_catalogs(
        ad_account_id: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        """List catalogs (retail, hotel or creative assets) and their IDs."""
        items, truncated = client.paginate(
            "/catalogs", _account(ad_account_id), max_results=max_results
        )
        return {"catalogs": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_list_catalog_feeds(
        catalog_id: str | None = None, ad_account_id: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        """List product feeds (data sources): location, format, schedule, status, country."""
        params = _account(ad_account_id)
        if catalog_id:
            params["catalog_id"] = segment(catalog_id, "catalog_id")
        items, truncated = client.paginate("/catalogs/feeds", params, max_results=max_results)
        return {"feeds": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_list_feed_processing_results(
        feed_id: str, ad_account_id: str | None = None, max_results: int = 10
    ) -> dict[str, Any]:
        """Recent processing runs of a feed: status, item counts, and error and warning totals.

        Use a result's id with pinterest_ads_list_item_issues to see item-level problems.
        """
        items, truncated = client.paginate(
            f"/catalogs/feeds/{segment(feed_id, 'feed_id')}/processing_results",
            _account(ad_account_id),
            max_results=max_results,
        )
        return {"processing_results": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_list_item_issues(
        processing_result_id: str,
        item_validation_issue: str | None = None,
        ad_account_id: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Item-level errors and warnings from one feed processing run."""
        params = _account(ad_account_id)
        if item_validation_issue:
            params["item_validation_issue"] = item_validation_issue
        items, truncated = client.paginate(
            f"/catalogs/processing_results/{segment(processing_result_id, 'processing_result_id')}"
            "/item_issues",
            params,
            max_results=max_results,
        )
        return {"item_issues": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_get_catalog_items(
        item_ids: list[str],
        country: str,
        language: str,
        catalog_id: str | None = None,
        catalog_type: str = "RETAIL",
        ad_account_id: str | None = None,
    ) -> Any:
        """Look up catalog items by item ID (your feed's id column). Changes nothing.

        country e.g. 'GB', language e.g. 'en-GB' or 'EN'. Up to 100 item IDs.
        """
        ids = [str(i).strip() for i in item_ids if str(i).strip()]
        if not ids or len(ids) > 100:
            raise ValueError("Pass 1 to 100 item IDs.")
        kind = catalog_type.upper()
        if kind not in CATALOG_TYPES:
            raise ValueError(f"catalog_type must be one of {', '.join(sorted(CATALOG_TYPES))}.")
        filters: dict[str, Any] = {"catalog_type": kind, "item_ids": ids}
        if catalog_id:
            filters["catalog_id"] = segment(catalog_id, "catalog_id")
        return client.request(
            "POST",
            "/catalogs/items",
            params=_account(ad_account_id),
            json_body={"country": country.upper(), "language": language, "filters": filters},
            read_only=True,
        )

    @mcp.tool()
    def pinterest_ads_ingest_catalog_feed(feed_id: str, ad_account_id: str | None = None) -> Any:
        """Ask Pinterest to fetch and process a feed now instead of waiting for its schedule."""
        return client.request(
            "POST",
            f"/catalogs/feeds/{segment(feed_id, 'feed_id')}/ingest",
            params=_account(ad_account_id),
            ad_account_id=ad_account_id,
            op="ingest_catalog_feed",
        )

    @mcp.tool()
    def pinterest_ads_batch_catalog_items(
        operation: str,
        items: list[dict[str, Any]],
        country: str,
        language: str,
        catalog_type: str = "RETAIL",
        catalog_id: str | None = None,
        ad_account_id: str | None = None,
        confirm: str = "",
    ) -> Any:
        """Create, update, upsert or delete catalog items in one batch (up to 1000).

        operation: CREATE, UPDATE (change some attributes), UPSERT (send every attribute) or
        DELETE. items: [{"item_id": "SKU1", "attributes": {"price": "24.99 GBP",
        "availability": "in stock"}}]; DELETE needs only item_id. If a feed manages the
        catalog, Pinterest recommends changing items through the feed. DELETE needs
        confirm='DELETE'; more than PINTEREST_ADS_BULK_THRESHOLD items need confirm='APPLY'.
        Returns a batch_id to check with pinterest_ads_get_catalog_items_batch.
        """
        op = operation.strip().upper()
        if op not in ITEM_OPERATIONS:
            raise ValueError(f"operation must be one of {', '.join(sorted(ITEM_OPERATIONS))}.")
        if not items or len(items) > MAX_BATCH_ITEMS:
            raise ValueError(f"Pass 1 to {MAX_BATCH_ITEMS} items.")
        kind = catalog_type.upper()
        if kind not in CATALOG_TYPES:
            raise ValueError(f"catalog_type must be one of {', '.join(sorted(CATALOG_TYPES))}.")
        rows = []
        for i, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("item_id"):
                raise ValueError(f"items[{i}] needs item_id.")
            row: dict[str, Any] = {"item_id": str(item["item_id"]), "operation": op}
            if op != "DELETE":
                if not isinstance(item.get("attributes"), dict):
                    raise ValueError(f"items[{i}] needs an attributes object.")
                row["attributes"] = item["attributes"]
            rows.append(row)
        body: dict[str, Any] = {
            "catalog_type": kind,
            "country": country.upper(),
            "language": language,
            "items": rows,
        }
        if catalog_id:
            body["catalog_id"] = segment(catalog_id, "catalog_id")
        decision = safety.assess(
            client.config,
            count=len(rows),
            destructive=safety.DELETE if op == "DELETE" else None,
        )
        refused = safety.refusal(
            client.config, decision, confirm, preview={**body, "items": rows[:20]}
        )
        if refused:
            return refused
        out = client.request(
            "POST",
            "/catalogs/items/batch",
            params=_account(ad_account_id),
            json_body=body,
            ad_account_id=ad_account_id,
            op=f"catalog_items_{op.lower()}",
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def pinterest_ads_get_catalog_items_batch(
        batch_id: str, ad_account_id: str | None = None
    ) -> Any:
        """Status of an item batch: whether it finished, and any per-item errors."""
        return client.request(
            "GET",
            f"/catalogs/items/batch/{segment(batch_id, 'batch_id')}",
            params=_account(ad_account_id),
        )

    @mcp.tool()
    def pinterest_ads_list_product_groups(
        catalog_id: str | None = None,
        feed_id: str | None = None,
        ids: list[str] | None = None,
        ad_account_id: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List catalog product groups (the sets of products catalog ads promote)."""
        params = _account(ad_account_id)
        if catalog_id:
            params["catalog_id"] = segment(catalog_id, "catalog_id")
        if feed_id:
            params["feed_id"] = segment(feed_id, "feed_id")
        if ids:
            params["id"] = ",".join(segment(i, "id") for i in ids[:1000])
        items, truncated = client.paginate(
            "/catalogs/product_groups", params, max_results=max_results
        )
        return {"product_groups": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_create_product_group(
        product_group: dict[str, Any], ad_account_id: str | None = None
    ) -> Any:
        """Create a catalog product group. Changes the account.

        product_group, for a retail catalog: {"catalog_id": "...", "catalog_type": "RETAIL",
        "country": "GB", "locale": "en-GB", "name": "Trainers under 50",
        "filters": {"all_of": [{"PRODUCT_TYPE_0": {"values": ["shoes"]}},
        {"MAX_PRICE": {"values": 50, "inclusion": true}}]}}. Feed-based groups use feed_id
        instead of catalog_id.
        """
        if not product_group.get("name") or not product_group.get("filters"):
            raise ValueError("product_group needs a name and filters.")
        return client.request(
            "POST",
            "/catalogs/product_groups",
            params=_account(ad_account_id),
            json_body=product_group,
            ad_account_id=ad_account_id,
            op="create_product_group",
        )

    @mcp.tool()
    def pinterest_ads_update_product_group(
        product_group_id: str, changes: dict[str, Any], ad_account_id: str | None = None
    ) -> Any:
        """Rename a product group or change its description, filters or featured flag.

        changes: e.g. {"name": "Trainers under 60", "filters": {...}}. For a retail catalog
        group include "catalog_type": "RETAIL". The old values are kept in the audit log.
        """
        if not changes:
            raise ValueError("Pass the fields to change.")
        path = f"/catalogs/product_groups/{segment(product_group_id, 'product_group_id')}"
        params = _account(ad_account_id)
        current = client.snapshot(client.request, "GET", path, params=params)
        before = (
            {k: current.get(k) for k in ["id", *changes] if k in current}
            if isinstance(current, dict)
            else None
        )
        return client.request(
            "PATCH",
            path,
            params=params,
            json_body=changes,
            ad_account_id=ad_account_id,
            op="update_product_group",
            before=before,
        )

    @mcp.tool()
    def pinterest_ads_delete_product_group(
        product_group_id: str, ad_account_id: str | None = None, confirm: str = ""
    ) -> Any:
        """Delete a catalog product group. Ads promoting it stop, and it can't be undone, so
        this needs confirm='DELETE' after the user agrees."""
        path = f"/catalogs/product_groups/{segment(product_group_id, 'product_group_id')}"
        params = _account(ad_account_id)
        decision = safety.assess(client.config, count=1, destructive=safety.DELETE)
        refused = safety.refusal(client.config, decision, confirm, preview={"delete": path})
        if refused:
            return refused
        current = client.snapshot(client.request, "GET", path, params=params)
        before = (
            {k: current.get(k) for k in ("id", "name", "filters") if k in current}
            if isinstance(current, dict)
            else None
        )
        out = client.request(
            "DELETE",
            path,
            params=params,
            ad_account_id=ad_account_id,
            op="delete_product_group",
            before=before,
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def pinterest_ads_get_product_group_product_counts(
        product_group_id: str, ad_account_id: str | None = None
    ) -> Any:
        """How many products a product group contains."""
        return client.request(
            "GET",
            f"/catalogs/product_groups/{segment(product_group_id, 'product_group_id')}"
            "/product_counts",
            params=_account(ad_account_id),
        )

    @mcp.tool()
    def pinterest_ads_list_product_group_products(
        product_group_id: str, ad_account_id: str | None = None, max_results: int = 50
    ) -> dict[str, Any]:
        """List the products (product Pins) in a product group."""
        items, truncated = client.paginate(
            f"/catalogs/product_groups/{segment(product_group_id, 'product_group_id')}/products",
            _account(ad_account_id),
            max_results=max_results,
        )
        return {"products": items, "count": len(items), "truncated": truncated}
