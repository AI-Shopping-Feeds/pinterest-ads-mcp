"""Tools through the real server (fake auth, mocked HTTP)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

import httpx
import pytest
import respx
from mcp import Client

from pinterest_ads_mcp import server
from pinterest_ads_mcp.config import Config

from .conftest import ACCOUNT, ACCOUNT_BASE, BASE, FakeAuth, audit_entries

M = 1_000_000


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "load_auth", lambda config: FakeAuth())


def _payload(result: Any) -> Any:
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


async def _call(config: Config, tool: str, args: dict[str, Any], **changes: Any) -> Any:
    mcp = server.build_server(dataclasses.replace(config, **changes))
    async with Client(mcp) as c:
        return await c.call_tool(tool, args)


def _items(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"items": [{"data": r, "exceptions": []} for r in rows]}


# ---------------------------------------------------------------- campaigns


@respx.mock
async def test_list_ads_builds_filters(config: Config) -> None:
    route = respx.get(f"{ACCOUNT_BASE}/ads").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "1"}], "bookmark": None})
    )
    out = _payload(
        await _call(config, "pinterest_ads_list_ads", {"campaign_ids": ["9"], "ids": ["1", "2"]})
    )
    assert out["count"] == 1
    params = route.calls.last.request.url.params
    assert params.get_list("campaign_ids") == ["9"]
    assert params.get_list("ad_ids") == ["1", "2"]
    # IDs are looked up in any status.
    assert params.get_list("entity_statuses") == ["ACTIVE", "PAUSED", "ARCHIVED", "DRAFT"]


@respx.mock
async def test_list_defaults_to_active_and_paused(config: Config) -> None:
    route = respx.get(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    _payload(await _call(config, "pinterest_ads_list_campaigns", {}))
    assert route.calls.last.request.url.params.get_list("entity_statuses") == ["ACTIVE", "PAUSED"]


async def test_unsupported_filter_is_an_error(config: Config) -> None:
    result = await _call(config, "pinterest_ads_list_campaigns", {"ad_group_ids": ["1"]})
    assert result.is_error
    assert "can't be filtered by ad_group_ids" in result.content[0].text


@respx.mock
async def test_create_campaigns_defaults_to_paused(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json=_items({"id": "77", "status": "PAUSED"}))
    )
    out = _payload(
        await _call(
            config,
            "pinterest_ads_create_campaigns",
            {"items": [{"name": "Spring", "objective_type": "SALES", "daily_spend_cap": 20 * M}]},
        )
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent == [
        {"name": "Spring", "objective_type": "SALES", "daily_spend_cap": 20 * M, "status": "PAUSED"}
    ]
    assert out["created_paused"] == 1 and out["succeeded"] == 1
    assert out["items"][0]["data"]["id"] == "77"


@pytest.mark.parametrize("objective", ["CATALOG_SALES", "WEB_CONVERSION", "VIDEO_VIEW"])
async def test_retiring_objectives_are_refused(config: Config, objective: str) -> None:
    result = await _call(
        config,
        "pinterest_ads_create_campaigns",
        {"items": [{"name": "x", "objective_type": objective}]},
    )
    assert result.is_error
    assert "1 Feb 2027" in result.content[0].text


@respx.mock
async def test_create_over_budget_cap_needs_apply(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json=_items({"id": "1"}))
    )
    args = {"items": [{"name": "x", "objective_type": "AWARENESS", "daily_spend_cap": 90 * M}]}
    refused = _payload(await _call(config, "pinterest_ads_create_campaigns", args, max_budget=50))
    assert refused["refused"] is True and refused["required_confirm"] == "APPLY"
    assert not route.called
    ok = _payload(
        await _call(
            config, "pinterest_ads_create_campaigns", {**args, "confirm": "APPLY"}, max_budget=50
        )
    )
    assert ok["succeeded"] == 1 and route.called


@respx.mock
async def test_update_budget_rise_is_guarded_and_snapshotted(config: Config, tmp_audit_log) -> None:
    respx.get(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "5", "name": "Brand", "status": "ACTIVE", "daily_spend_cap": 10 * M}
                ]
            },
        )
    )
    patch = respx.patch(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json=_items({"id": "5", "status": "ACTIVE"}))
    )
    args = {"items": [{"id": "5", "daily_spend_cap": 15 * M}]}
    refused = _payload(await _call(config, "pinterest_ads_update_campaigns", args))
    assert refused["required_confirm"] == "APPLY"
    assert refused["money_changes"][0]["change_pct"] == 50.0
    assert not patch.called

    small = {"items": [{"id": "5", "daily_spend_cap": 11 * M}]}
    _payload(await _call(config, "pinterest_ads_update_campaigns", small))
    assert json.loads(patch.calls.last.request.content) == small["items"]
    [entry] = audit_entries(tmp_audit_log)
    # Only the changed field (and status) is kept from the current state.
    assert entry["before"] == [{"id": "5", "daily_spend_cap": 10 * M, "status": "ACTIVE"}]


@respx.mock
async def test_update_to_archived_needs_archive(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/ad_groups").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    result = _payload(
        await _call(
            config, "pinterest_ads_update_ad_groups", {"items": [{"id": "1", "status": "ARCHIVED"}]}
        )
    )
    assert result["required_confirm"] == "ARCHIVE"


@respx.mock
async def test_archive_ad_groups(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/ad_groups").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "3", "name": "AG", "status": "PAUSED"}]}
        )
    )
    patch = respx.patch(f"{ACCOUNT_BASE}/ad_groups").mock(
        return_value=httpx.Response(200, json=_items({"id": "3", "status": "ARCHIVED"}))
    )
    refused = _payload(await _call(config, "pinterest_ads_archive_ad_groups", {"ids": ["3"]}))
    assert refused["required_confirm"] == "ARCHIVE" and not patch.called
    _payload(
        await _call(config, "pinterest_ads_archive_ad_groups", {"ids": ["3"], "confirm": "ARCHIVE"})
    )
    assert json.loads(patch.calls.last.request.content) == [{"id": "3", "status": "ARCHIVED"}]


@respx.mock
async def test_creates_are_sent_thirty_at_a_time(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/ads").mock(
        side_effect=lambda request: httpx.Response(
            200, json=_items(*[{"id": str(i)} for i in json.loads(request.content)])
        )
    )
    items = [{"ad_group_id": "1", "pin_id": str(i), "creative_type": "REGULAR"} for i in range(45)]
    out = _payload(
        await _call(config, "pinterest_ads_create_ads", {"items": items, "confirm": "APPLY"})
    )
    assert [len(json.loads(c.request.content)) for c in route.calls] == [30, 15]
    assert out["succeeded"] == 45 and len(out["audit_ids"]) == 2


@respx.mock
async def test_dry_run_rehearses_without_refusing(config: Config) -> None:
    route = respx.patch(f"{ACCOUNT_BASE}/campaigns").mock(return_value=httpx.Response(200))
    respx.get(f"{ACCOUNT_BASE}/campaigns").mock(return_value=httpx.Response(200, json={}))
    out = _payload(
        await _call(
            config,
            "pinterest_ads_archive_campaigns",
            {"ids": ["1"]},
            dry_run=True,
        )
    )
    assert out["dry_run"] is True and out["safety"]["required_confirm"] == "ARCHIVE"
    assert not route.called


@respx.mock
async def test_product_group_promotions_wrap_the_ad_group(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/product_group_promotions").mock(
        return_value=httpx.Response(200, json=_items({"id": "8"}))
    )
    _payload(
        await _call(
            config,
            "pinterest_ads_create_product_group_promotions",
            {"ad_group_id": "44", "items": [{"catalog_product_group_id": "9"}]},
        )
    )
    assert json.loads(route.calls.last.request.content) == {
        "ad_group_id": "44",
        "product_group_promotion": [{"catalog_product_group_id": "9", "status": "PAUSED"}],
    }


# ---------------------------------------------------------------- bulk


@respx.mock
async def test_adjust_bids_by_percentage(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/ad_groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "1", "bid_in_micro_currency": 1 * M},
                    {"id": "2", "bid_in_micro_currency": 2 * M},
                    {"id": "3", "bid_in_micro_currency": None},
                ]
            },
        )
    )
    patch = respx.patch(f"{ACCOUNT_BASE}/ad_groups").mock(
        return_value=httpx.Response(200, json=_items({"id": "1"}, {"id": "2"}))
    )
    out = _payload(
        await _call(
            config,
            "pinterest_ads_adjust_ad_group_bids",
            {"ids": ["1", "2", "3", "4"], "change_pct": -10},
        )
    )
    assert json.loads(patch.calls.last.request.content) == [
        {"id": "1", "bid_in_micro_currency": 900000},
        {"id": "2", "bid_in_micro_currency": 1800000},
    ]
    assert out["not_found"] == ["4"]
    assert out["skipped"][0]["id"] == "3"


@respx.mock
async def test_adjust_budgets_rise_needs_apply(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "1", "daily_spend_cap": 10 * M}]})
    )
    patch = respx.patch(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json=_items({"id": "1"}))
    )
    args = {"entity": "campaigns", "ids": ["1"], "set_budget": 30}
    refused = _payload(await _call(config, "pinterest_ads_adjust_budgets", args))
    assert refused["required_confirm"] == "APPLY" and not patch.called
    _payload(await _call(config, "pinterest_ads_adjust_budgets", {**args, "confirm": "APPLY"}))
    assert json.loads(patch.calls.last.request.content) == [{"id": "1", "daily_spend_cap": 30 * M}]


@respx.mock
async def test_set_statuses_over_threshold_needs_apply(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/ads").mock(return_value=httpx.Response(200, json={"items": []}))
    patch = respx.patch(f"{ACCOUNT_BASE}/ads").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    ids = [str(i) for i in range(1, 13)]
    refused = _payload(
        await _call(
            config, "pinterest_ads_set_statuses", {"entity": "ads", "ids": ids, "status": "ACTIVE"}
        )
    )
    assert refused["required_confirm"] == "APPLY" and not patch.called
    _payload(
        await _call(
            config,
            "pinterest_ads_set_statuses",
            {"entity": "ads", "ids": ids[:2], "status": "paused"},
        )
    )
    assert json.loads(patch.calls.last.request.content) == [
        {"id": "1", "status": "PAUSED"},
        {"id": "2", "status": "PAUSED"},
    ]


@respx.mock
async def test_bulk_upsert_always_needs_confirm(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/bulk/upsert").mock(
        return_value=httpx.Response(200, json={"request_id": "r1"})
    )
    args = {"create": {"campaigns": [{"name": "x", "objective_type": "AWARENESS"}]}}
    refused = _payload(await _call(config, "pinterest_ads_bulk_upsert", args))
    assert refused["required_confirm"] == "APPLY" and not route.called
    out = _payload(await _call(config, "pinterest_ads_bulk_upsert", {**args, "confirm": "APPLY"}))
    assert out["request_id"] == "r1"
    body = json.loads(route.calls.last.request.content)
    assert body["create"]["campaigns"][0]["status"] == "PAUSED"
    archive = {"update": {"ads": [{"id": "1", "status": "ARCHIVED"}]}}
    assert (
        _payload(await _call(config, "pinterest_ads_bulk_upsert", archive))["required_confirm"]
        == "ARCHIVE"
    )


@respx.mock
async def test_bulk_export_polls_and_downloads(config: Config) -> None:
    url = "https://pinterest-bulk.s3.amazonaws.com/export.json?sig=1"
    respx.post(f"{ACCOUNT_BASE}/bulk/download").mock(
        return_value=httpx.Response(200, json={"request_id": "r9"})
    )
    respx.get(f"{ACCOUNT_BASE}/bulk/r9").mock(
        side_effect=[
            httpx.Response(200, json={"status": "RUNNING"}),
            httpx.Response(200, json={"status": "SUCCEEDED", "result_url": url}),
        ]
    )
    respx.get(url).mock(return_value=httpx.Response(200, json=[{"id": "1"}, {"id": "2"}]))
    out = _payload(
        await _call(
            config, "pinterest_ads_bulk_export", {"entity_types": ["campaign"], "poll_seconds": 0}
        )
    )
    assert out["status"] == "SUCCEEDED" and out["row_count"] == 2


# ---------------------------------------------------------------- keywords & targeting


@respx.mock
async def test_negative_keywords_at_campaign_level(config: Config) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"keywords": [{"id": "k1"}], "errors": []})
    )
    out = _payload(
        await _call(
            config,
            "pinterest_ads_create_negative_keywords",
            {"parent_id": "626747269410", "keywords": ["free", {"value": "cheap"}]},
        )
    )
    assert json.loads(route.calls.last.request.content) == {
        "parent_id": "626747269410",
        "keywords": [
            {"value": "free", "match_type": "PHRASE_NEGATIVE"},
            {"value": "cheap", "match_type": "PHRASE_NEGATIVE"},
        ],
    }
    assert out["created"] == 1


async def test_positive_match_type_rejected_for_negatives(config: Config) -> None:
    result = await _call(
        config,
        "pinterest_ads_create_negative_keywords",
        {"parent_id": "1", "keywords": [{"value": "x", "match_type": "BROAD"}]},
    )
    assert result.is_error


@respx.mock
async def test_archive_keywords_needs_archive(config: Config) -> None:
    route = respx.patch(f"{ACCOUNT_BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"keywords": [], "errors": []})
    )
    refused = _payload(await _call(config, "pinterest_ads_archive_keywords", {"ids": ["1"]}))
    assert refused["required_confirm"] == "ARCHIVE" and not route.called
    _payload(
        await _call(config, "pinterest_ads_archive_keywords", {"ids": ["1"], "confirm": "ARCHIVE"})
    )
    assert json.loads(route.calls.last.request.content) == {
        "keywords": [{"id": "1", "archived": True}]
    }


@respx.mock
async def test_customer_list_is_hashed_and_kept_out_of_the_audit_log(
    config: Config, tmp_audit_log
) -> None:
    route = respx.post(f"{ACCOUNT_BASE}/customer_lists").mock(
        return_value=httpx.Response(200, json={"id": "cl1"})
    )
    already = hashlib.sha256(b"x").hexdigest()
    _payload(
        await _call(
            config,
            "pinterest_ads_create_customer_list",
            {"name": "Buyers", "identifiers": [" Person@Example.com ", already]},
        )
    )
    sent = json.loads(route.calls.last.request.content)
    expected = hashlib.sha256(b"person@example.com").hexdigest()
    assert sent["records"] == f"{expected},{already}"
    assert sent["list_type"] == "EMAIL"
    log = tmp_audit_log.read_text()
    assert expected not in log and "example.com" not in log
    assert "<2 identifiers>" in log


@respx.mock
async def test_delete_audience_needs_delete(config: Config) -> None:
    respx.get(f"{ACCOUNT_BASE}/audiences/a1").mock(
        return_value=httpx.Response(200, json={"id": "a1", "name": "Old"})
    )
    route = respx.patch(f"{ACCOUNT_BASE}/audiences/a1").mock(
        return_value=httpx.Response(200, json={"id": "a1"})
    )
    refused = _payload(await _call(config, "pinterest_ads_delete_audience", {"audience_id": "a1"}))
    assert refused["required_confirm"] == "DELETE" and not route.called
    _payload(
        await _call(
            config, "pinterest_ads_delete_audience", {"audience_id": "a1", "confirm": "DELETE"}
        )
    )
    assert json.loads(route.calls.last.request.content)["operation_type"] == "REMOVE"


@respx.mock
async def test_targeting_options_are_filtered(config: Config) -> None:
    respx.get(f"{BASE}/resources/targeting/LOCATION").mock(
        return_value=httpx.Response(200, json={"GB": "United Kingdom", "US": "United States"})
    )
    out = _payload(
        await _call(
            config,
            "pinterest_ads_get_targeting_options",
            {"targeting_type": "location", "search": "kingdom"},
        )
    )
    assert out["options"] == {"GB": "United Kingdom"} and out["count"] == 1


# ---------------------------------------------------------------- catalogs & pins


@respx.mock
async def test_batch_catalog_delete_needs_delete(config: Config) -> None:
    route = respx.post(f"{BASE}/catalogs/items/batch").mock(
        return_value=httpx.Response(200, json={"batch_id": "b1"})
    )
    args = {
        "operation": "DELETE",
        "items": [{"item_id": "SKU1"}],
        "country": "gb",
        "language": "en-GB",
    }
    assert (
        _payload(await _call(config, "pinterest_ads_batch_catalog_items", args))["required_confirm"]
        == "DELETE"
    )
    _payload(
        await _call(config, "pinterest_ads_batch_catalog_items", {**args, "confirm": "DELETE"})
    )
    body = json.loads(route.calls.last.request.content)
    assert body["items"] == [{"item_id": "SKU1", "operation": "DELETE"}]
    assert body["country"] == "GB"


@respx.mock
async def test_delete_product_group_needs_delete(config: Config) -> None:
    respx.get(f"{BASE}/catalogs/product_groups/55").mock(
        return_value=httpx.Response(200, json={"id": "55", "name": "Shoes"})
    )
    route = respx.delete(f"{BASE}/catalogs/product_groups/55").mock(
        return_value=httpx.Response(204)
    )
    refused = _payload(
        await _call(config, "pinterest_ads_delete_product_group", {"product_group_id": "55"})
    )
    assert refused["required_confirm"] == "DELETE" and not route.called
    _payload(
        await _call(
            config,
            "pinterest_ads_delete_product_group",
            {"product_group_id": "55", "confirm": "DELETE"},
        )
    )
    assert route.called


@respx.mock
async def test_create_ad_pin(config: Config) -> None:
    route = respx.post(f"{BASE}/pins").mock(return_value=httpx.Response(201, json={"id": "p1"}))
    _payload(
        await _call(
            config,
            "pinterest_ads_create_ad_pin",
            {
                "image_url": "https://example.com/a.jpg",
                "link": "https://example.com/p",
                "ad_account_id": ACCOUNT,
                "ai_disclosures": ["ai_modified"],
            },
        )
    )
    req = route.calls.last.request
    body = json.loads(req.content)
    assert body["is_removable"] is True
    assert body["media_source"]["source_type"] == "image_url"
    assert body["ai_disclosures"] == {"values": ["AI_MODIFIED"]}
    assert req.url.params["ad_account_id"] == ACCOUNT


async def test_create_ad_pin_needs_https(config: Config) -> None:
    result = await _call(
        config,
        "pinterest_ads_create_ad_pin",
        {"image_url": "http://example.com/a.jpg", "link": "https://example.com"},
    )
    assert result.is_error


# ---------------------------------------------------------------- analytics


@respx.mock
async def test_analytics_paths_and_params(config: Config) -> None:
    sync = respx.get(f"{ACCOUNT_BASE}/campaigns/analytics").mock(
        return_value=httpx.Response(200, json=[{"CAMPAIGN_ID": 1}])
    )
    targeting = respx.get(f"{ACCOUNT_BASE}/ad_groups/targeting_analytics").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    base = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "columns": ["spend_in_dollar", "CTR"],
    }
    _payload(
        await _call(
            config, "pinterest_ads_get_analytics", {**base, "level": "campaign", "ids": ["1", "2"]}
        )
    )
    params = sync.calls.last.request.url.params
    assert params["columns"] == "SPEND_IN_DOLLAR,CTR"
    assert params.get_list("campaign_ids") == ["1", "2"]
    assert params["granularity"] == "TOTAL"
    _payload(
        await _call(
            config,
            "pinterest_ads_get_analytics",
            {**base, "level": "ad_group", "ids": ["3"], "targeting_types": ["location"]},
        )
    )
    assert targeting.calls.last.request.url.params["targeting_types"] == "LOCATION"
    missing = await _call(config, "pinterest_ads_get_analytics", {**base, "level": "ad"})
    assert missing.is_error


# ---------------------------------------------------------------- escape hatch


@respx.mock
async def test_read_request_refuses_writes(config: Config) -> None:
    result = await _call(
        config, "pinterest_ads_read_request", {"method": "PATCH", "path": "/ad_accounts/1/ads"}
    )
    assert result.is_error and "pinterest_ads_request" in result.content[0].text
    route = respx.post(f"{BASE}/ad_accounts/1/ads/targeting_analytics").mock(
        return_value=httpx.Response(200, json={})
    )
    sizing = respx.post(f"{BASE}/ad_accounts/1/ad_groups/audience_sizing").mock(
        return_value=httpx.Response(200, json={"size": 1})
    )
    _payload(
        await _call(
            config,
            "pinterest_ads_read_request",
            {"method": "POST", "path": "/v5/ad_accounts/1/ad_groups/audience_sizing"},
        )
    )
    assert sizing.called and not route.called


@respx.mock
async def test_raw_write_needs_apply_and_oauth_is_blocked(config: Config, tmp_audit_log) -> None:
    route = respx.post(f"{BASE}/ad_accounts/1/labels").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    args = {"method": "POST", "path": "/ad_accounts/1/labels", "body": [{"name": "x"}]}
    refused = _payload(await _call(config, "pinterest_ads_request", args))
    assert refused["required_confirm"] == "APPLY" and not route.called
    _payload(await _call(config, "pinterest_ads_request", {**args, "confirm": "APPLY"}))
    assert route.called
    assert audit_entries(tmp_audit_log)[-1]["op"] == "request POST /ad_accounts/1/labels"
    blocked = await _call(
        config,
        "pinterest_ads_request",
        {"method": "POST", "path": "/oauth/token/revoke", "confirm": "APPLY"},
    )
    assert blocked.is_error
    traversal = await _call(
        config, "pinterest_ads_read_request", {"method": "GET", "path": "/ad_accounts/../oauth"}
    )
    assert traversal.is_error


# ---------------------------------------------------------------- paths of the simpler tools

A = f"/v5/ad_accounts/{ACCOUNT}"
SIMPLE_CALLS: list[tuple[str, dict[str, Any], str, str]] = [
    ("pinterest_ads_list_ad_accounts", {}, "GET", "/v5/ad_accounts"),
    ("pinterest_ads_get_ad_account", {}, "GET", A),
    ("pinterest_ads_get_user_account", {}, "GET", "/v5/user_account"),
    ("pinterest_ads_list_businesses", {}, "GET", "/v5/businesses/employers"),
    (
        "pinterest_ads_list_business_assets",
        {"business_id": "42"},
        "GET",
        "/v5/businesses/42/assets",
    ),
    (
        "pinterest_ads_get_bid_floors",
        {"bid_floor_specs": [{"billable_event": "CLICKTHROUGH"}]},
        "POST",
        f"{A}/bid_floor",
    ),
    ("pinterest_ads_list_pins", {"pin_filter": "has_been_promoted"}, "GET", "/v5/pins"),
    ("pinterest_ads_get_pin", {"pin_id": "77"}, "GET", "/v5/pins/77"),
    ("pinterest_ads_list_boards", {}, "GET", "/v5/boards"),
    ("pinterest_ads_list_board_pins", {"board_id": "9"}, "GET", "/v5/boards/9/pins"),
    ("pinterest_ads_list_keywords", {"ad_group_id": "5"}, "GET", f"{A}/keywords"),
    (
        "pinterest_ads_get_keyword_metrics",
        {"keywords": ["linen dress"], "country_code": "gb"},
        "GET",
        f"{A}/keywords/metrics",
    ),
    ("pinterest_ads_list_targeting_templates", {}, "GET", f"{A}/targeting_templates"),
    (
        "pinterest_ads_estimate_audience_size",
        {"targeting_spec": {"LOCATION": ["GB"]}},
        "POST",
        f"{A}/ad_groups/audience_sizing",
    ),
    ("pinterest_ads_list_audiences", {}, "GET", f"{A}/audiences"),
    ("pinterest_ads_get_audience", {"audience_id": "3"}, "GET", f"{A}/audiences/3"),
    (
        "pinterest_ads_create_audience",
        {"name": "Visitors", "audience_type": "visitor", "rule": {"retention_days": 30}},
        "POST",
        f"{A}/audiences",
    ),
    ("pinterest_ads_list_customer_lists", {}, "GET", f"{A}/customer_lists"),
    (
        "pinterest_ads_update_customer_list",
        {"customer_list_id": "8", "identifiers": ["a@example.com"], "operation": "remove"},
        "PATCH",
        f"{A}/customer_lists/8",
    ),
    ("pinterest_ads_list_conversion_tags", {}, "GET", f"{A}/conversion_tags"),
    (
        "pinterest_ads_get_conversion_tag",
        {"conversion_tag_id": "6"},
        "GET",
        f"{A}/conversion_tags/6",
    ),
    ("pinterest_ads_list_catalogs", {}, "GET", "/v5/catalogs"),
    ("pinterest_ads_list_catalog_feeds", {"catalog_id": "1"}, "GET", "/v5/catalogs/feeds"),
    (
        "pinterest_ads_list_feed_processing_results",
        {"feed_id": "2"},
        "GET",
        "/v5/catalogs/feeds/2/processing_results",
    ),
    (
        "pinterest_ads_list_item_issues",
        {"processing_result_id": "4"},
        "GET",
        "/v5/catalogs/processing_results/4/item_issues",
    ),
    (
        "pinterest_ads_get_catalog_items",
        {"item_ids": ["SKU1"], "country": "GB", "language": "en-GB"},
        "POST",
        "/v5/catalogs/items",
    ),
    ("pinterest_ads_ingest_catalog_feed", {"feed_id": "2"}, "POST", "/v5/catalogs/feeds/2/ingest"),
    (
        "pinterest_ads_get_catalog_items_batch",
        {"batch_id": "b1"},
        "GET",
        "/v5/catalogs/items/batch/b1",
    ),
    (
        "pinterest_ads_list_product_groups",
        {"ids": ["1", "2"]},
        "GET",
        "/v5/catalogs/product_groups",
    ),
    (
        "pinterest_ads_create_product_group",
        {"product_group": {"name": "Shoes", "filters": {"all_of": []}}},
        "POST",
        "/v5/catalogs/product_groups",
    ),
    (
        "pinterest_ads_get_product_group_product_counts",
        {"product_group_id": "7"},
        "GET",
        "/v5/catalogs/product_groups/7/product_counts",
    ),
    (
        "pinterest_ads_list_product_group_products",
        {"product_group_id": "7"},
        "GET",
        "/v5/catalogs/product_groups/7/products",
    ),
    ("pinterest_ads_list_metrics", {"search": "spend"}, "GET", "/v5/resources/delivery_metrics"),
    (
        "pinterest_ads_get_metrics_ready_state",
        {"date": "2026-10-01"},
        "GET",
        "/v5/resources/metrics_ready_state",
    ),
    ("pinterest_ads_get_audience_insights", {}, "GET", f"{A}/audience_insights"),
    ("pinterest_ads_list_report_templates", {}, "GET", f"{A}/templates"),
    ("pinterest_ads_get_bulk_request", {"request_id": "r1"}, "GET", f"{A}/bulk/r1"),
]


@pytest.mark.parametrize(("tool", "args", "method", "path"), SIMPLE_CALLS)
async def test_simple_tools_call_the_right_endpoint(
    config: Config, tool: str, args: dict[str, Any], method: str, path: str
) -> None:
    with respx.mock() as router:
        route = router.route(host="api.pinterest.com").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = await _call(config, tool, args)
    assert not result.is_error, result.content
    calls = [(c.request.method, c.request.url.path) for c in route.calls]
    assert (method, path) in calls, calls


@respx.mock
async def test_update_product_group_keeps_old_values(config: Config, tmp_audit_log) -> None:
    respx.get(f"{BASE}/catalogs/product_groups/7").mock(
        return_value=httpx.Response(200, json={"id": "7", "name": "Old", "filters": {"a": 1}})
    )
    route = respx.patch(f"{BASE}/catalogs/product_groups/7").mock(
        return_value=httpx.Response(200, json={"id": "7"})
    )
    _payload(
        await _call(
            config,
            "pinterest_ads_update_product_group",
            {"product_group_id": "7", "changes": {"name": "New"}},
        )
    )
    assert json.loads(route.calls.last.request.content) == {"name": "New"}
    assert audit_entries(tmp_audit_log)[0]["before"] == {"id": "7", "name": "Old"}


@respx.mock
async def test_suggest_keywords_calls_both_endpoints(config: Config) -> None:
    suggested = respx.get(f"{BASE}/terms/suggested").mock(
        return_value=httpx.Response(200, json=["linen dress"])
    )
    related = respx.get(f"{BASE}/terms/related").mock(
        return_value=httpx.Response(200, json={"related_terms_list": []})
    )
    out = _payload(await _call(config, "pinterest_ads_suggest_keywords", {"term": "linen"}))
    assert out["suggested"] == ["linen dress"]
    assert suggested.calls.last.request.url.params["term"] == "linen"
    assert related.calls.last.request.url.params.get_list("terms") == ["linen"]
