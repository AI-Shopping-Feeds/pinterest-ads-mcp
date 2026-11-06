"""The HTTP choke point: headers, scoping, retries, pacing, dry-run, audit, downloads."""

from __future__ import annotations

import dataclasses
import gzip
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from pinterest_ads_mcp.audit import AuditLog
from pinterest_ads_mcp.client import (
    PinterestAdsClient,
    PinterestAPIError,
    check_path,
    parse_rows,
    segment,
)
from pinterest_ads_mcp.config import Config

from .conftest import ACCOUNT, ACCOUNT_BASE, BASE, FakeAuth, audit_entries

S3 = "https://pinterest-reports.s3.amazonaws.com/report.json?sig=abc"


@respx.mock
def test_headers_and_base_url(client: PinterestAdsClient) -> None:
    route = respx.get(f"{BASE}/ad_accounts").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    client.request("GET", "/ad_accounts", params={"page_size": 5, "skip": None})
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer pina_fakeaccesstokenvalue"
    assert req.headers["Accept"] == "application/json"
    assert req.headers["User-Agent"].startswith("pinterest-ads-mcp/")
    assert req.url.params["page_size"] == "5"
    assert "skip" not in req.url.params


@respx.mock
def test_sandbox_host(config: Config, audit: AuditLog) -> None:
    sandbox = PinterestAdsClient(dataclasses.replace(config, sandbox=True), FakeAuth(), audit)
    route = respx.get("https://api-sandbox.pinterest.com/v5/ad_accounts").mock(
        return_value=httpx.Response(200, json={})
    )
    sandbox.request("GET", "/ad_accounts")
    assert route.called


def test_ad_account_resolution(client: PinterestAdsClient) -> None:
    assert client.resolve_ad_account(None) == ACCOUNT
    assert client.resolve_ad_account("555") == "555"
    assert client.account_path(None, "campaigns") == f"/ad_accounts/{ACCOUNT}/campaigns"
    with pytest.raises(ValueError, match="numeric"):
        client.resolve_ad_account("12/../34")
    client.config.ad_account_id = None
    with pytest.raises(ValueError, match="PINTEREST_ADS_AD_ACCOUNT_ID"):
        client.resolve_ad_account(None)


@pytest.mark.parametrize(
    "path",
    [
        "ad_accounts",
        "/ad_accounts/../oauth",
        "//evil.example",
        "/ad_accounts?x=1",
        "/a#b",
        "/",
        "/oauth/token",
        "/ad accounts",
        "https://evil.example/x",
    ],
)
def test_bad_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        check_path(path)


def test_v5_prefix_is_stripped_and_segments_checked() -> None:
    assert check_path("/v5/ad_accounts") == "/ad_accounts"
    assert segment("123_abc-9") == "123_abc-9"
    with pytest.raises(ValueError):
        segment("12/34")


@respx.mock
def test_dry_run_sends_nothing_and_audits(client: PinterestAdsClient) -> None:
    client.config.dry_run = True
    route = respx.post(f"{ACCOUNT_BASE}/campaigns").mock(return_value=httpx.Response(200))
    out = client.request(
        "POST",
        f"/ad_accounts/{ACCOUNT}/campaigns",
        json_body=[{"name": "x"}],
        ad_account_id=ACCOUNT,
        op="create_campaigns",
    )
    assert out["_dry_run"] is True and out["body"] == [{"name": "x"}]
    assert not route.called
    [entry] = audit_entries(client.audit.path)
    assert entry["dry_run"] is True and entry["op"] == "create_campaigns"
    assert entry["audit_id"] == out["audit_id"]


@respx.mock
def test_read_only_post_bypasses_dry_run(client: PinterestAdsClient) -> None:
    client.config.dry_run = True
    route = respx.post(f"{ACCOUNT_BASE}/reports").mock(
        return_value=httpx.Response(200, json={"token": "t"})
    )
    assert client.request("POST", f"/ad_accounts/{ACCOUNT}/reports", json_body={}, read_only=True)
    assert route.called
    assert audit_entries(client.audit.path) == []


@respx.mock
def test_write_is_audited_with_a_summary_and_snapshot(client: PinterestAdsClient) -> None:
    respx.patch(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "data": {"id": "1", "status": "PAUSED", "name": "secret name"},
                        "exceptions": [],
                    }
                ]
            },
        )
    )
    out = client.request(
        "PATCH",
        f"/ad_accounts/{ACCOUNT}/campaigns",
        json_body=[{"id": "1", "status": "PAUSED"}],
        ad_account_id=ACCOUNT,
        before=[{"id": "1", "status": "ACTIVE"}],
    )
    [entry] = audit_entries(client.audit.path)
    assert entry["audit_id"] == out["audit_id"]
    assert entry["response_summary"] == {"items": [{"id": "1", "status": "PAUSED"}]}
    assert entry["before"] == [{"id": "1", "status": "ACTIVE"}]
    assert "secret name" not in client.audit.path.read_text()


@respx.mock
def test_snapshots_can_be_turned_off(client: PinterestAdsClient) -> None:
    client.config.audit_snapshots = False
    respx.patch(f"{ACCOUNT_BASE}/ads").mock(return_value=httpx.Response(200, json={}))
    client.request("PATCH", f"/ad_accounts/{ACCOUNT}/ads", json_body=[], before=[{"id": "1"}])
    assert audit_entries(client.audit.path)[0]["before"] is None


@respx.mock
def test_audit_body_replaces_the_request_body(client: PinterestAdsClient) -> None:
    respx.post(f"{ACCOUNT_BASE}/customer_lists").mock(return_value=httpx.Response(200, json={}))
    client.request(
        "POST",
        f"/ad_accounts/{ACCOUNT}/customer_lists",
        json_body={"records": "abc,def"},
        audit_body={"records": "<2 identifiers>"},
    )
    assert audit_entries(client.audit.path)[0]["request_body"] == {"records": "<2 identifiers>"}


@respx.mock
def test_failed_write_is_audited_and_raised(client: PinterestAdsClient) -> None:
    respx.post(f"{ACCOUNT_BASE}/campaigns").mock(
        return_value=httpx.Response(400, json={"code": 4042, "message": "Budget too small"})
    )
    with pytest.raises(PinterestAPIError, match="code 4042: Budget too small"):
        client.request("POST", f"/ad_accounts/{ACCOUNT}/campaigns", json_body=[{}])
    [entry] = audit_entries(client.audit.path)
    assert entry["error"] == "HTTP 400" and entry["response_status"] == 400


@respx.mock
def test_401_refreshes_once(client: PinterestAdsClient, fake_auth: FakeAuth) -> None:
    route = respx.get(f"{BASE}/user_account").mock(
        side_effect=[httpx.Response(401, json={"code": 2}), httpx.Response(200, json={"a": 1})]
    )
    assert client.request("GET", "/user_account") == {"a": 1}
    assert fake_auth.invalidated == 1 and route.call_count == 2


@respx.mock
def test_401_hint(client: PinterestAdsClient) -> None:
    respx.get(f"{BASE}/user_account").mock(return_value=httpx.Response(401, json={"code": 2}))
    with pytest.raises(PinterestAPIError, match="pinterest-ads-mcp auth"):
        client.request("GET", "/user_account")


@respx.mock
def test_403_pinner_data_hint(client: PinterestAdsClient) -> None:
    respx.get(f"{BASE}/pins/1").mock(
        return_value=httpx.Response(403, json={"code": 403, "message": "PINNER_DATA_ACCESS_DENIED"})
    )
    with pytest.raises(PinterestAPIError, match="don't retry"):
        client.request("GET", "/pins/1")


@respx.mock
def test_429_backs_off_with_retry_after(client: PinterestAdsClient, _no_sleep: list[float]) -> None:
    route = respx.get(f"{BASE}/ad_accounts").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={"code": 8}),
            httpx.Response(200, json={"items": []}),
        ]
    )
    client.request("GET", "/ad_accounts")
    assert route.call_count == 2
    assert 7 <= _no_sleep[0] < 8


@respx.mock
def test_exhausted_window_pauses_the_next_request(
    client: PinterestAdsClient, _no_sleep: list[float]
) -> None:
    respx.get(f"{BASE}/ad_accounts").mock(
        return_value=httpx.Response(
            200,
            json={},
            headers={
                "x-ratelimit-limit": "100, 100;w=1",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "3",
            },
        )
    )
    client.request("GET", "/ad_accounts")
    assert _no_sleep == []
    client.request("GET", "/ad_accounts")
    assert len(_no_sleep) == 1 and 0 < _no_sleep[0] <= 3
    assert client.last_rate_limit["remaining"] == "0"


@respx.mock
def test_paginate_follows_bookmarks(client: PinterestAdsClient) -> None:
    route = respx.get(f"{BASE}/ad_accounts").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}], "bookmark": "b1"}),
            httpx.Response(200, json={"items": [{"id": 3}], "bookmark": None}),
        ]
    )
    items, truncated = client.paginate("/ad_accounts", {}, max_results=10)
    assert [i["id"] for i in items] == [1, 2, 3] and truncated is False
    assert route.calls[1].request.url.params["bookmark"] == "b1"
    assert route.calls[0].request.url.params["page_size"] == "10"


@respx.mock
def test_paginate_truncates(client: PinterestAdsClient) -> None:
    respx.get(f"{BASE}/ad_accounts").mock(
        return_value=httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}], "bookmark": "b"})
    )
    items, truncated = client.paginate("/ad_accounts", {}, max_results=2)
    assert len(items) == 2 and truncated is True


def test_list_params_are_repeated(client: PinterestAdsClient) -> None:
    with respx.mock() as router:
        route = router.get(f"{ACCOUNT_BASE}/ads").mock(return_value=httpx.Response(200, json={}))
        client.request("GET", f"/ad_accounts/{ACCOUNT}/ads", params={"ad_ids": ["1", "2"]})
    assert route.calls.last.request.url.params.get_list("ad_ids") == ["1", "2"]


# ---------------------------------------------------------------- downloads


def test_download_refuses_other_hosts(client: PinterestAdsClient) -> None:
    for url in (
        "https://evil.example/r.json",
        "http://x.s3.amazonaws.com/r",
        "ftp://pinterest.com",
    ):
        with pytest.raises(ValueError, match="isn't Pinterest's"):
            client.download(url)


@respx.mock
def test_download_sends_no_token_and_unzips(client: PinterestAdsClient) -> None:
    route = respx.get(S3).mock(
        return_value=httpx.Response(200, content=gzip.compress(b'[{"a": 1}]'))
    )
    assert client.download(S3) == b'[{"a": 1}]'
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_download_zip(client: PinterestAdsClient) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rows.json", '[{"b": 2}]')
    respx.get(S3).mock(return_value=httpx.Response(200, content=buf.getvalue()))
    assert parse_rows(client.download(S3)) == [{"b": 2}]


@respx.mock
def test_download_is_capped(client: PinterestAdsClient) -> None:
    client.config.max_download_mb = 1
    respx.get(S3).mock(return_value=httpx.Response(200, content=b"x" * (1024 * 1024 + 10)))
    with pytest.raises(ValueError, match="PINTEREST_ADS_MAX_DOWNLOAD_MB"):
        client.download(S3)


@respx.mock
def test_gzip_bomb_is_capped(client: PinterestAdsClient) -> None:
    client.config.max_download_mb = 1
    respx.get(S3).mock(
        return_value=httpx.Response(200, content=gzip.compress(b"0" * (3 * 1024 * 1024)))
    )
    with pytest.raises(ValueError, match="larger than"):
        client.download(S3)


def test_parse_rows_formats() -> None:
    grouped = json.dumps({"111": [{"SPEND": 1}], "222": [{"SPEND": 2}]}).encode()
    assert parse_rows(grouped) == [{"SPEND": 1, "_group": "111"}, {"SPEND": 2, "_group": "222"}]
    assert parse_rows(b'[{"a": 1}]') == [{"a": 1}]
    assert parse_rows(b"a,b\n1,2\n", "CSV") == [{"a": "1", "b": "2"}]
    assert parse_rows(b'{"a": 1}\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]
    assert parse_rows(b"") == []


def test_audit_file_is_private(client: PinterestAdsClient, tmp_audit_log: Path) -> None:
    import os
    import stat

    client.audit.record(op="x", resource="/r", method="POST", url="u")
    if os.name == "posix":
        assert stat.S_IMODE(tmp_audit_log.stat().st_mode) == 0o600
        assert stat.S_IMODE(tmp_audit_log.parent.stat().st_mode) == 0o700
