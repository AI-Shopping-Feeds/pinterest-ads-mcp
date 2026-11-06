"""Asynchronous ads reports: request, poll, download (or one call that does all three).

Pinterest builds the report in the background and returns a short-lived link.
The file is downloaded into memory (capped by PINTEREST_ADS_MAX_DOWNLOAD_MB),
parsed and returned; it is never written to disk or the audit log.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.mcpserver import Context

from ..client import PinterestAdsClient, parse_rows, segment
from ._progress import report, run_blocking
from .analytics import COLUMNS_HELP, GRANULARITIES, WINDOWS

LEVELS = {
    "ADVERTISER",
    "ADVERTISER_TARGETING",
    "CAMPAIGN",
    "CAMPAIGN_TARGETING",
    "AD_GROUP",
    "AD_GROUP_TARGETING",
    "PIN_PROMOTION",
    "PIN_PROMOTION_TARGETING",
    "KEYWORD",
    "PRODUCT_GROUP",
    "PRODUCT_GROUP_TARGETING",
    "PRODUCT_ITEM",
    "PRODUCT_ITEM_TARGETING",
}
FILTER_KEYS = {
    "campaign_ids",
    "campaign_statuses",
    "campaign_objective_types",
    "ad_group_ids",
    "ad_group_statuses",
    "ad_ids",
    "ad_statuses",
    "product_group_ids",
    "product_group_statuses",
    "product_item_ids",
    "metrics_filters",
    "campaign_custom_label",
    "campaign_brand_label",
    "custom_conversion_event_metrics",
    "start_hour",
    "end_hour",
    "primary_sort",
    "combine_targeting_types",
}
DONE = "FINISHED"
FAILED = {"FAILED", "CANCELLED", "EXPIRED", "DOES_NOT_EXIST"}

REPORT_HELP = (
    "level: ADVERTISER, CAMPAIGN, AD_GROUP, PIN_PROMOTION (ads), KEYWORD, PRODUCT_GROUP, "
    "PRODUCT_ITEM, or a *_TARGETING level with targeting_types (up to 5, e.g. ['LOCATION']). "
    "Dates are YYYY-MM-DD; granularity TOTAL, DAY, WEEK, MONTH or HOUR. filters adds other "
    'request fields, e.g. {"campaign_ids": ["..."], "campaign_statuses": ["RUNNING"]} '
    "(campaign_objective_types should include SALES and LEADS, not only the retiring "
    "CATALOG_SALES and WEB_CONVERSION). report_format JSON (default) or CSV. To re-run a "
    "report saved in Ads Manager, pass template_id (see pinterest_ads_list_report_templates) "
    "with dates and granularity only.\n\n" + COLUMNS_HELP
)


def _body(
    *,
    level: str,
    start_date: str,
    end_date: str,
    columns: list[str],
    granularity: str,
    targeting_types: list[str] | None,
    filters: dict[str, Any] | None,
    report_format: str,
    click_window_days: int | None,
    engagement_window_days: int | None,
    view_window_days: int | None,
    conversion_report_time: str | None,
    reporting_timezone: str | None,
) -> dict[str, Any]:
    lvl = level.strip().upper()
    if lvl not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(sorted(LEVELS))}.")
    gran = granularity.strip().upper()
    if gran not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {', '.join(sorted(GRANULARITIES))}.")
    if not columns:
        raise ValueError("Pass at least one column.")
    fmt = report_format.strip().upper()
    if fmt not in {"JSON", "CSV"}:
        raise ValueError("report_format must be JSON or CSV.")
    body: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "granularity": gran,
        "level": lvl,
        "columns": [c.strip().upper() for c in columns if c.strip()],
        "report_format": fmt,
    }
    if targeting_types:
        if not lvl.endswith("_TARGETING"):
            raise ValueError("targeting_types needs a level ending in _TARGETING.")
        body["targeting_types"] = [t.upper() for t in targeting_types]
    elif lvl.endswith("_TARGETING"):
        raise ValueError(f"level {lvl} needs targeting_types, e.g. ['LOCATION'].")
    for name, value in (
        ("click_window_days", click_window_days),
        ("engagement_window_days", engagement_window_days),
        ("view_window_days", view_window_days),
    ):
        if value is not None:
            if value not in WINDOWS:
                raise ValueError(f"{name} must be one of {sorted(WINDOWS)}.")
            body[name] = value
    if conversion_report_time:
        body["conversion_report_time"] = conversion_report_time.upper()
    if reporting_timezone:
        body["reporting_timezone"] = reporting_timezone.upper()
    unknown = sorted(set(filters or {}) - FILTER_KEYS)
    if unknown:
        raise ValueError(f"Unknown filters {unknown}; supported: {', '.join(sorted(FILTER_KEYS))}.")
    body.update(filters or {})
    return body


def register(mcp, client: PinterestAdsClient) -> None:
    def create(
        body: dict[str, Any] | None,
        template_id: str | None,
        template_params: dict[str, Any],
        ad_account_id: str | None,
    ) -> dict[str, Any]:
        if template_id:
            path = client.account_path(
                ad_account_id, "templates", segment(template_id, "template_id"), "reports"
            )
            return client.request("POST", path, params=template_params, read_only=True)
        return client.request(
            "POST", client.account_path(ad_account_id, "reports"), json_body=body, read_only=True
        )

    def status(token: str, ad_account_id: str | None) -> dict[str, Any]:
        out = client.request(
            "GET", client.account_path(ad_account_id, "reports"), params={"token": token}
        )
        return out if isinstance(out, dict) else {"result": out}

    def rows_payload(token: str, st: dict[str, Any], fmt: str, max_rows: int) -> dict[str, Any]:
        rows = parse_rows(client.download(str(st["url"])), fmt)
        return {
            "token": token,
            "report_status": st.get("report_status"),
            "row_count": len(rows),
            "rows": rows[: max(0, max_rows)],
            "truncated": len(rows) > max_rows,
        }

    def prepare(
        level: str | None,
        start_date: str,
        end_date: str,
        columns: list[str] | None,
        granularity: str,
        targeting_types: list[str] | None,
        filters: dict[str, Any] | None,
        report_format: str,
        windows: tuple[int | None, int | None, int | None],
        conversion_report_time: str | None,
        reporting_timezone: str | None,
        template_id: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if template_id:
            return None, {
                "start_date": start_date,
                "end_date": end_date,
                "granularity": granularity.upper(),
            }
        if not level:
            raise ValueError("Pass level (or template_id).")
        return (
            _body(
                level=level,
                start_date=start_date,
                end_date=end_date,
                columns=columns or [],
                granularity=granularity,
                targeting_types=targeting_types,
                filters=filters,
                report_format=report_format,
                click_window_days=windows[0],
                engagement_window_days=windows[1],
                view_window_days=windows[2],
                conversion_report_time=conversion_report_time,
                reporting_timezone=reporting_timezone,
            ),
            {},
        )

    @mcp.tool(
        description=(
            "Request an ads report. Pinterest builds it in the background: poll "
            "pinterest_ads_get_report with the returned token, then call "
            "pinterest_ads_download_report (or use pinterest_ads_run_report to do all "
            "three). Changes nothing in the account.\n\n" + REPORT_HELP
        )
    )
    def pinterest_ads_create_report(
        start_date: str,
        end_date: str,
        level: str | None = None,
        columns: list[str] | None = None,
        granularity: str = "DAY",
        targeting_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        report_format: str = "JSON",
        click_window_days: int | None = None,
        engagement_window_days: int | None = None,
        view_window_days: int | None = None,
        conversion_report_time: str | None = None,
        reporting_timezone: str | None = None,
        template_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        body, template_params = prepare(
            level,
            start_date,
            end_date,
            columns,
            granularity,
            targeting_types,
            filters,
            report_format,
            (click_window_days, engagement_window_days, view_window_days),
            conversion_report_time,
            reporting_timezone,
            template_id,
        )
        return create(body, template_id, template_params, ad_account_id)

    @mcp.tool()
    def pinterest_ads_get_report(token: str, ad_account_id: str | None = None) -> dict[str, Any]:
        """Check a report's status: IN_PROGRESS, FINISHED, FAILED, CANCELLED or EXPIRED.

        When FINISHED, call pinterest_ads_download_report with the same token.
        """
        st = status(token, ad_account_id)
        st.pop("url", None)  # short-lived signed link; download_report fetches it
        return st

    @mcp.tool()
    def pinterest_ads_download_report(
        token: str,
        max_rows: int = 1000,
        report_format: str = "JSON",
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Download a FINISHED report and return its rows (up to max_rows), held in memory only.

        row_count is the full count; truncated says whether rows were cut to max_rows.
        report_format must match the one the report was requested in.
        """
        st = status(token, ad_account_id)
        if st.get("report_status") != DONE or not st.get("url"):
            return {
                "token": token,
                "report_status": st.get("report_status"),
                "message": "Report isn't ready; check again with pinterest_ads_get_report.",
            }
        return rows_payload(token, st, report_format, max_rows)

    @mcp.tool(
        description=(
            "Request a report, wait for it with progress updates, and return its rows. Same "
            "arguments as pinterest_ads_create_report, plus max_rows (default 1000), "
            "timeout_seconds (default 600) and poll_seconds (default 10). If it times out the "
            "token is returned so you can check later with pinterest_ads_get_report. Changes "
            "nothing in the account.\n\n" + REPORT_HELP
        )
    )
    async def pinterest_ads_run_report(
        start_date: str,
        end_date: str,
        level: str | None = None,
        columns: list[str] | None = None,
        granularity: str = "DAY",
        targeting_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        report_format: str = "JSON",
        click_window_days: int | None = None,
        engagement_window_days: int | None = None,
        view_window_days: int | None = None,
        conversion_report_time: str | None = None,
        reporting_timezone: str | None = None,
        template_id: str | None = None,
        max_rows: int = 1000,
        timeout_seconds: float = 600,
        poll_seconds: float = 10,
        ad_account_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        body, template_params = prepare(
            level,
            start_date,
            end_date,
            columns,
            granularity,
            targeting_types,
            filters,
            report_format,
            (click_window_days, engagement_window_days, view_window_days),
            conversion_report_time,
            reporting_timezone,
            template_id,
        )
        created = await run_blocking(create, body, template_id, template_params, ad_account_id)
        token = created.get("token") if isinstance(created, dict) else None
        if not token:
            raise RuntimeError(f"No report token in the response: {created}")
        step = 1
        await report(ctx, step, None, "Requested report")
        deadline = time.monotonic() + timeout_seconds
        while True:
            st = await run_blocking(status, token, ad_account_id)
            state = st.get("report_status")
            step += 1
            if state == DONE and st.get("url"):
                await report(ctx, step, None, "Report ready; downloading")
                payload = await run_blocking(rows_payload, token, st, report_format, max_rows)
                await report(ctx, step + 1, None, f"Downloaded {payload['row_count']} rows")
                return payload
            if state in FAILED:
                return {"token": token, "report_status": state, "message": st.get("message")}
            if time.monotonic() >= deadline:
                return {
                    "token": token,
                    "report_status": state,
                    "message": "Still running. Check later with pinterest_ads_get_report.",
                }
            await report(ctx, step, None, f"Report {state}; waiting")
            await asyncio.sleep(max(0.0, poll_seconds))

    @mcp.tool()
    def pinterest_ads_list_report_templates(
        ad_account_id: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        """List custom report templates saved in Ads Manager, to re-run them by template_id."""
        items, truncated = client.paginate(
            client.account_path(ad_account_id, "templates"), {}, max_results=max_results
        )
        return {"templates": items, "count": len(items), "truncated": truncated}
