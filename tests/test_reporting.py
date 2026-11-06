"""Async reports: create -> poll -> download, through the MCP client."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest
import respx
from mcp import Client

from pinterest_ads_mcp import server
from pinterest_ads_mcp.config import Config

from .conftest import ACCOUNT_BASE, FakeAuth, audit_entries

TOKEN = "report-token-123"
URL = "https://pinterest-reports.s3.amazonaws.com/async/report.json?sig=abc"
REPORT = {"111": [{"SPEND_IN_DOLLAR": 1.5, "DATE": "2026-10-01"}], "222": [{"SPEND_IN_DOLLAR": 2}]}
ARGS = {
    "level": "campaign",
    "start_date": "2026-10-01",
    "end_date": "2026-10-31",
    "columns": ["SPEND_IN_DOLLAR", "campaign_name"],
}


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "load_auth", lambda config: FakeAuth())


def _mock_flow(router, final: str = "FINISHED"):
    create = router.post(f"{ACCOUNT_BASE}/reports").mock(
        return_value=httpx.Response(200, json={"token": TOKEN, "report_status": "IN_PROGRESS"})
    )
    status = router.get(f"{ACCOUNT_BASE}/reports").mock(
        side_effect=[
            httpx.Response(200, json={"report_status": "IN_PROGRESS"}),
            httpx.Response(200, json={"report_status": final, "url": URL, "size": 10}),
        ]
    )
    download = router.get(URL).mock(
        return_value=httpx.Response(200, content=gzip.compress(json.dumps(REPORT).encode()))
    )
    return create, status, download


async def test_run_report_creates_polls_and_downloads(config: Config, tmp_audit_log) -> None:
    progress: list[tuple[float, str | None]] = []

    async def on_progress(p: float, total: float | None, message: str | None) -> None:
        progress.append((p, message))

    with respx.mock() as router:
        create, status, download = _mock_flow(router)
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool(
                "pinterest_ads_run_report",
                {**ARGS, "poll_seconds": 0, "max_rows": 1},
                progress_callback=on_progress,
            )
    assert not result.is_error, result.content
    out = result.structured_content or json.loads(result.content[0].text)
    assert out["row_count"] == 2
    assert out["rows"] == [{"SPEND_IN_DOLLAR": 1.5, "DATE": "2026-10-01", "_group": "111"}]
    assert out["truncated"] is True
    body = json.loads(create.calls.last.request.content)
    assert body == {
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "granularity": "DAY",
        "level": "CAMPAIGN",
        "columns": ["SPEND_IN_DOLLAR", "CAMPAIGN_NAME"],
        "report_format": "JSON",
    }
    assert status.calls.last.request.url.params["token"] == TOKEN
    assert "Authorization" not in download.calls.last.request.headers
    steps = [p for p, _ in progress]
    assert steps == sorted(steps) and len(set(steps)) == len(steps)
    assert any("Downloaded 2 rows" in (m or "") for _, m in progress)
    # Report requests and rows never reach the audit log.
    assert audit_entries(tmp_audit_log) == []


async def test_run_report_reports_failure(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        _mock_flow(router, final="FAILED")
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool("pinterest_ads_run_report", {**ARGS, "poll_seconds": 0})
    out = result.structured_content or json.loads(result.content[0].text)
    assert out["report_status"] == "FAILED"


async def test_run_report_times_out_with_token(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{ACCOUNT_BASE}/reports").mock(
            return_value=httpx.Response(200, json={"token": TOKEN})
        )
        router.get(f"{ACCOUNT_BASE}/reports").mock(
            return_value=httpx.Response(200, json={"report_status": "IN_PROGRESS"})
        )
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool(
                "pinterest_ads_run_report", {**ARGS, "poll_seconds": 0, "timeout_seconds": 0}
            )
    out = result.structured_content or json.loads(result.content[0].text)
    assert out["token"] == TOKEN and "Still running" in out["message"]


async def test_get_report_hides_the_signed_url(config: Config) -> None:
    with respx.mock() as router:
        router.get(f"{ACCOUNT_BASE}/reports").mock(
            return_value=httpx.Response(200, json={"report_status": "FINISHED", "url": URL})
        )
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool("pinterest_ads_get_report", {"token": TOKEN})
    out = result.structured_content or json.loads(result.content[0].text)
    assert out == {"report_status": "FINISHED"}


async def test_download_report_not_ready(config: Config) -> None:
    with respx.mock() as router:
        router.get(f"{ACCOUNT_BASE}/reports").mock(
            return_value=httpx.Response(200, json={"report_status": "IN_PROGRESS"})
        )
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool("pinterest_ads_download_report", {"token": TOKEN})
    out = result.structured_content or json.loads(result.content[0].text)
    assert "isn't ready" in out["message"]


async def test_template_reports_use_the_template_endpoint(config: Config) -> None:
    with respx.mock() as router:
        route = router.post(f"{ACCOUNT_BASE}/templates/777/reports").mock(
            return_value=httpx.Response(200, json={"token": TOKEN})
        )
        async with Client(server.build_server(config)) as c:
            result = await c.call_tool(
                "pinterest_ads_create_report",
                {"template_id": "777", "start_date": "2026-10-01", "end_date": "2026-10-07"},
            )
    assert not result.is_error
    params = route.calls.last.request.url.params
    assert params["start_date"] == "2026-10-01" and params["granularity"] == "DAY"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"level": "CAMPAIGN_TARGETING"}, "needs targeting_types"),
        ({"targeting_types": ["LOCATION"]}, "ending in _TARGETING"),
        ({"filters": {"bogus": 1}}, "Unknown filters"),
        ({"level": "nope"}, "level must be one of"),
    ],
)
async def test_bad_report_requests(config: Config, args: dict, message: str) -> None:
    async with Client(server.build_server(config)) as c:
        result = await c.call_tool("pinterest_ads_create_report", {**ARGS, **args})
    assert result.is_error
    assert message in result.content[0].text
