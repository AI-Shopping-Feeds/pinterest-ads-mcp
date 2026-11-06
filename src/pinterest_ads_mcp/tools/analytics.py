"""Synchronous ads analytics: quick metrics for an account or up to 250 entities.

For large date ranges or many entities, use the reporting tools (async reports).
Results are returned to the client and never stored.
"""

from __future__ import annotations

from typing import Any

from ..client import PinterestAdsClient
from ._common import check_ids

# level -> (path under the ad account, id query parameter or None)
LEVELS: dict[str, tuple[str, str | None]] = {
    "account": ("", None),
    "campaign": ("campaigns", "campaign_ids"),
    "ad_group": ("ad_groups", "ad_group_ids"),
    "ad": ("ads", "ad_ids"),
    "product_group": ("product_groups", "product_group_ids"),
}
GRANULARITIES = {"TOTAL", "DAY", "HOUR", "WEEK", "MONTH"}
WINDOWS = {0, 1, 7, 14, 30, 60}
COLUMNS_HELP = """\
Common columns: SPEND_IN_DOLLAR (account currency, despite the name), PAID_IMPRESSION,
IMPRESSION_1, CLICKTHROUGH_1 (Pin clicks), OUTBOUND_CLICK_1, CTR, OUTBOUND_CTR_1,
CPC_IN_MICRO_DOLLAR, CPM_IN_DOLLAR, TOTAL_ENGAGEMENT, TOTAL_CONVERSIONS,
TOTAL_CHECKOUT, TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR, CHECKOUT_ROAS, TOTAL_ADD_TO_CART,
TOTAL_LEAD, TOTAL_SIGNUP, CAMPAIGN_NAME, CAMPAIGN_ID. _1 metrics are paid, _2 earned
(saves of ads). pinterest_ads_list_metrics lists every column with its definition.
HOUR granularity no longer includes conversion metrics."""


def build_params(
    *,
    start_date: str,
    end_date: str,
    columns: list[str],
    granularity: str,
    targeting_types: list[str] | None,
    click_window_days: int | None,
    engagement_window_days: int | None,
    view_window_days: int | None,
    conversion_report_time: str | None,
    reporting_timezone: str | None,
    attribution_types: list[str] | None,
) -> dict[str, Any]:
    if not columns:
        raise ValueError("Pass at least one column.")
    gran = granularity.upper()
    if gran not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {', '.join(sorted(GRANULARITIES))}.")
    params: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "columns": ",".join(c.strip().upper() for c in columns if c.strip()),
        "granularity": gran,
    }
    for name, value in (
        ("click_window_days", click_window_days),
        ("engagement_window_days", engagement_window_days),
        ("view_window_days", view_window_days),
    ):
        if value is not None:
            if value not in WINDOWS:
                raise ValueError(f"{name} must be one of {sorted(WINDOWS)}.")
            params[name] = value
    if conversion_report_time:
        params["conversion_report_time"] = conversion_report_time.upper()
    if reporting_timezone:
        params["reporting_timezone"] = reporting_timezone.upper()
    if targeting_types:
        params["targeting_types"] = ",".join(t.upper() for t in targeting_types)
    if attribution_types:
        params["attribution_types"] = ",".join(a.upper() for a in attribution_types)
    return params


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool(
        description=(
            "Get ads metrics right away (synchronous) for the ad account, or for up to 250 "
            "campaigns, ad groups, ads or product groups. Changes nothing.\n\n"
            "level: account, campaign, ad_group, ad or product_group (ids required except "
            "for account). Dates are YYYY-MM-DD. granularity: TOTAL, DAY, WEEK, MONTH or HOUR. "
            "targeting_types breaks results down by targeting, e.g. ['LOCATION'], "
            "['KEYWORD'], ['AGE_BUCKET_AND_GENDER'] (not for product groups). Attribution "
            "windows default to Pinterest's (30-day click and engagement, 1-day view); "
            "conversion_report_time TIME_OF_AD_ACTION (default) or TIME_OF_CONVERSION; "
            "reporting_timezone PINTEREST_TIME_ZONE (UTC, default) or AD_ACCOUNT_TIME_ZONE.\n\n"
            + COLUMNS_HELP
        )
    )
    def pinterest_ads_get_analytics(
        level: str,
        start_date: str,
        end_date: str,
        columns: list[str],
        ids: list[str] | None = None,
        granularity: str = "TOTAL",
        targeting_types: list[str] | None = None,
        click_window_days: int | None = None,
        engagement_window_days: int | None = None,
        view_window_days: int | None = None,
        conversion_report_time: str | None = None,
        reporting_timezone: str | None = None,
        attribution_types: list[str] | None = None,
        ad_account_id: str | None = None,
    ) -> Any:
        key = level.strip().lower()
        if key not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}.")
        segment_path, id_param = LEVELS[key]
        params = build_params(
            start_date=start_date,
            end_date=end_date,
            columns=columns,
            granularity=granularity,
            targeting_types=targeting_types,
            click_window_days=click_window_days,
            engagement_window_days=engagement_window_days,
            view_window_days=view_window_days,
            conversion_report_time=conversion_report_time,
            reporting_timezone=reporting_timezone,
            attribution_types=attribution_types if targeting_types else None,
        )
        if id_param:
            id_list = check_ids(ids, "ids", 250)
            if not id_list:
                raise ValueError(f"level '{key}' needs ids (up to 250).")
            params[id_param] = id_list
        if targeting_types and key == "product_group":
            raise ValueError("Targeting breakdowns aren't available for product groups.")
        leaf = "targeting_analytics" if targeting_types else "analytics"
        parts = [p for p in (segment_path, leaf) if p]
        return client.request("GET", client.account_path(ad_account_id, *parts), params=params)

    @mcp.tool()
    def pinterest_ads_list_metrics(
        search: str | None = None, report_type: str | None = None
    ) -> dict[str, Any]:
        """Definitions of the metrics (columns) reports can return, with Ads Manager names.

        search filters by name or definition, e.g. 'checkout'. report_type: SYNC or ASYNC.
        """
        params = {"report_type": report_type.upper()} if report_type else {}
        data = client.request("GET", "/resources/delivery_metrics", params=params)
        metrics = data.get("items", data) if isinstance(data, dict) else data
        rows = metrics if isinstance(metrics, list) else [metrics]
        needle = (search or "").strip().lower()
        if needle:
            rows = [r for r in rows if needle in str(r).lower()]
        return {"metrics": rows[:300], "count": len(rows)}

    @mcp.tool()
    def pinterest_ads_get_metrics_ready_state(date: str) -> Any:
        """Whether conversion and other metrics for a date (YYYY-MM-DD) are final yet."""
        return client.request("GET", "/resources/metrics_ready_state", params={"date": date})

    @mcp.tool()
    def pinterest_ads_get_audience_insights(
        audience_insight_type: str = "YOUR_ENGAGED_AUDIENCE", ad_account_id: str | None = None
    ) -> Any:
        """Interests and demographics of the account's audience over the last 30 days.

        audience_insight_type: YOUR_ENGAGED_AUDIENCE (default), YOUR_TOTAL_AUDIENCE or
        PINTEREST_TOTAL_AUDIENCE. Aggregated data only; don't use it for competitor or
        platform benchmarking.
        """
        return client.request(
            "GET",
            client.account_path(ad_account_id, "audience_insights"),
            params={"audience_insight_type": audience_insight_type.upper()},
        )
