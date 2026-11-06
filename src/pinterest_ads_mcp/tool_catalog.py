"""Title and MCP annotations for every pinterest_ads_* tool.

`PinterestAdsMCPServer.tool()` looks each tool up here by name, so the tool
modules stay free of metadata. Every tool must have an entry:
tests/test_mcp_client.py fails if a registered tool is missing or an entry has
no tool.

Annotation kinds (all API tools are open-world: they call the Pinterest API):

* READ: changes nothing in the account, including read-only POSTs such as
  audience sizing, bid floors and catalog item lookups.
* REPORT: requests a report or export. Changes nothing in the account, but each
  call starts a new background job, so it isn't idempotent.
* WRITE: creates something; repeating it creates it again.
* UPSERT: an update or state change; repeating it changes nothing more.
* DESTRUCTIVE: archives, deletes or removes; Pinterest can't undo these.
* DESTRUCTIVE_ONCE: bulk jobs and the raw request tool, which may do anything.
* LOCAL_*: touches only this server (audit log, session settings).
"""

from __future__ import annotations

from typing import NamedTuple

from mcp.types import ToolAnnotations


def _ann(*, read_only: bool, destructive: bool, idempotent: bool, open_world: bool = True):
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


READ = _ann(read_only=True, destructive=False, idempotent=True)
REPORT = _ann(read_only=True, destructive=False, idempotent=False)
WRITE = _ann(read_only=False, destructive=False, idempotent=False)
UPSERT = _ann(read_only=False, destructive=False, idempotent=True)
DESTRUCTIVE = _ann(read_only=False, destructive=True, idempotent=True)
DESTRUCTIVE_ONCE = _ann(read_only=False, destructive=True, idempotent=False)
LOCAL_READ = _ann(read_only=True, destructive=False, idempotent=True, open_world=False)
LOCAL_SETTING = _ann(read_only=False, destructive=False, idempotent=True, open_world=False)


class ToolSpec(NamedTuple):
    title: str
    annotations: ToolAnnotations


# Registered regardless of PINTEREST_ADS_TOOL_GROUPS and PINTEREST_ADS_READ_ONLY.
ALWAYS_REGISTERED = frozenset(
    {
        "pinterest_ads_describe_server",
        "pinterest_ads_health_check",
        "pinterest_ads_audit_lookup",
        "pinterest_ads_audit_tail",
        "pinterest_ads_set_dry_run",
    }
)

_CATALOG: dict[str, tuple[str, ToolAnnotations]] = {
    # server (meta)
    "pinterest_ads_describe_server": ("Describe the server configuration", LOCAL_READ),
    "pinterest_ads_health_check": ("Check the token and list ad accounts", READ),
    "pinterest_ads_audit_lookup": ("Look up an audit log entry", LOCAL_READ),
    "pinterest_ads_audit_tail": ("Show recent audit log entries", LOCAL_READ),
    "pinterest_ads_set_dry_run": ("Turn dry-run mode on or off", LOCAL_SETTING),
    # accounts
    "pinterest_ads_list_ad_accounts": ("List ad accounts", READ),
    "pinterest_ads_get_ad_account": ("Get an ad account", READ),
    "pinterest_ads_get_user_account": ("Get the signed-in Pinterest account", READ),
    "pinterest_ads_list_businesses": ("List businesses", READ),
    "pinterest_ads_list_business_assets": ("List a business's assets", READ),
    # campaigns
    "pinterest_ads_list_campaigns": ("List campaigns", READ),
    "pinterest_ads_create_campaigns": ("Create campaigns", WRITE),
    "pinterest_ads_update_campaigns": ("Update campaigns", UPSERT),
    "pinterest_ads_archive_campaigns": ("Archive campaigns", DESTRUCTIVE),
    # ad_groups
    "pinterest_ads_list_ad_groups": ("List ad groups", READ),
    "pinterest_ads_create_ad_groups": ("Create ad groups", WRITE),
    "pinterest_ads_update_ad_groups": ("Update ad groups", UPSERT),
    "pinterest_ads_archive_ad_groups": ("Archive ad groups", DESTRUCTIVE),
    "pinterest_ads_get_bid_floors": ("Get minimum bids", READ),
    # ads
    "pinterest_ads_list_ads": ("List ads", READ),
    "pinterest_ads_create_ads": ("Create ads from Pins", WRITE),
    "pinterest_ads_update_ads": ("Update ads", UPSERT),
    "pinterest_ads_archive_ads": ("Archive ads", DESTRUCTIVE),
    "pinterest_ads_list_product_group_promotions": ("List product group promotions", READ),
    "pinterest_ads_create_product_group_promotions": ("Create product group promotions", WRITE),
    "pinterest_ads_update_product_group_promotions": ("Update product group promotions", UPSERT),
    # pins
    "pinterest_ads_list_pins": ("List Pins to promote", READ),
    "pinterest_ads_get_pin": ("Get a Pin", READ),
    "pinterest_ads_list_boards": ("List boards", READ),
    "pinterest_ads_list_board_pins": ("List the Pins on a board", READ),
    "pinterest_ads_create_ad_pin": ("Create an ad-only Pin", WRITE),
    # keywords
    "pinterest_ads_list_keywords": ("List keywords", READ),
    "pinterest_ads_create_keywords": ("Add keywords to an ad group", WRITE),
    "pinterest_ads_create_negative_keywords": ("Add negative keywords", WRITE),
    "pinterest_ads_archive_keywords": ("Archive keywords", DESTRUCTIVE),
    "pinterest_ads_get_keyword_metrics": ("Get keyword search volume", READ),
    "pinterest_ads_suggest_keywords": ("Suggest keywords", READ),
    # targeting
    "pinterest_ads_get_targeting_options": ("Get targeting options", READ),
    "pinterest_ads_list_targeting_templates": ("List targeting templates", READ),
    "pinterest_ads_estimate_audience_size": ("Estimate audience size", READ),
    "pinterest_ads_list_audiences": ("List audiences", READ),
    "pinterest_ads_get_audience": ("Get an audience", READ),
    "pinterest_ads_create_audience": ("Create an audience", WRITE),
    "pinterest_ads_update_audience": ("Update an audience", UPSERT),
    "pinterest_ads_delete_audience": ("Remove an audience", DESTRUCTIVE),
    "pinterest_ads_list_customer_lists": ("List customer lists", READ),
    "pinterest_ads_create_customer_list": ("Upload a customer list", WRITE),
    "pinterest_ads_update_customer_list": ("Add or remove customer list records", DESTRUCTIVE_ONCE),
    # conversions
    "pinterest_ads_list_conversion_tags": ("List conversion tags", READ),
    "pinterest_ads_get_conversion_tag": ("Get a conversion tag", READ),
    # catalogs
    "pinterest_ads_list_catalogs": ("List catalogs", READ),
    "pinterest_ads_list_catalog_feeds": ("List catalog feeds", READ),
    "pinterest_ads_list_feed_processing_results": ("List feed processing results", READ),
    "pinterest_ads_list_item_issues": ("List catalog item issues", READ),
    "pinterest_ads_get_catalog_items": ("Look up catalog items", READ),
    "pinterest_ads_ingest_catalog_feed": ("Process a catalog feed now", WRITE),
    "pinterest_ads_batch_catalog_items": ("Change catalog items in a batch", DESTRUCTIVE_ONCE),
    "pinterest_ads_get_catalog_items_batch": ("Check a catalog item batch", READ),
    "pinterest_ads_list_product_groups": ("List product groups", READ),
    "pinterest_ads_create_product_group": ("Create a product group", WRITE),
    "pinterest_ads_update_product_group": ("Update a product group", UPSERT),
    "pinterest_ads_delete_product_group": ("Delete a product group", DESTRUCTIVE),
    "pinterest_ads_get_product_group_product_counts": ("Count products in a product group", READ),
    "pinterest_ads_list_product_group_products": ("List products in a product group", READ),
    # analytics
    "pinterest_ads_get_analytics": ("Get ads analytics", READ),
    "pinterest_ads_list_metrics": ("List metric definitions", READ),
    "pinterest_ads_get_metrics_ready_state": ("Check whether metrics are final", READ),
    "pinterest_ads_get_audience_insights": ("Get audience insights", READ),
    # reporting
    "pinterest_ads_create_report": ("Request an ads report", REPORT),
    "pinterest_ads_get_report": ("Check a report's status", READ),
    "pinterest_ads_download_report": ("Download a finished report", READ),
    "pinterest_ads_run_report": ("Run a report and return its rows", REPORT),
    "pinterest_ads_list_report_templates": ("List saved report templates", READ),
    # bulk
    "pinterest_ads_set_statuses": ("Pause or activate many entities", UPSERT),
    "pinterest_ads_adjust_ad_group_bids": ("Adjust many ad group bids", WRITE),
    "pinterest_ads_adjust_budgets": ("Adjust many budgets", WRITE),
    "pinterest_ads_bulk_upsert": ("Create or update entities in bulk", DESTRUCTIVE_ONCE),
    "pinterest_ads_get_bulk_request": ("Check a bulk job", READ),
    "pinterest_ads_bulk_export": ("Export ad entities in bulk", REPORT),
    # request
    "pinterest_ads_read_request": ("Read from any Pinterest API endpoint", READ),
    "pinterest_ads_request": ("Call any Pinterest API endpoint", DESTRUCTIVE_ONCE),
}

TOOL_SPECS: dict[str, ToolSpec] = {
    name: ToolSpec(title, annotations) for name, (title, annotations) in _CATALOG.items()
}
