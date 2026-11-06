"""Keywords and negative keywords, plus keyword research.

Positive keywords (BROAD, PHRASE, EXACT) belong to ad groups. Negative keywords
(EXACT_NEGATIVE, PHRASE_NEGATIVE) can sit on an ad group, a campaign or the
whole ad account. Keyword bids are deprecated by Pinterest and ignored, so
these tools don't take them.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import PinterestAdsClient
from ._common import check_ids, merge_results
from ._progress import report, run_blocking

POSITIVE = {"BROAD", "PHRASE", "EXACT"}
NEGATIVE = {"EXACT_NEGATIVE", "PHRASE_NEGATIVE"}
KEYWORD_BATCH = 100
MAX_KEYWORDS = 1000
_PARENT_RE = re.compile(r"^(AG|C)?\d+$")


def _keyword_objects(
    keywords: list[Any], default_match: str, allowed: set[str]
) -> list[dict[str, Any]]:
    if not keywords:
        raise ValueError("Pass at least one keyword.")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError(f"At most {MAX_KEYWORDS} keywords per call.")
    out = []
    for i, kw in enumerate(keywords):
        if isinstance(kw, str):
            value, match = kw, default_match
        elif isinstance(kw, dict) and kw.get("value"):
            value, match = str(kw["value"]), str(kw.get("match_type") or default_match)
        else:
            raise ValueError(f"keywords[{i}] must be a string or {{'value', 'match_type'}}.")
        match = match.upper()
        if match not in allowed:
            raise ValueError(
                f"keywords[{i}]: match_type must be one of {', '.join(sorted(allowed))}."
            )
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError(f"keywords[{i}] must be 1 to 120 characters.")
        out.append({"value": value, "match_type": match})
    return out


async def _create(
    client: PinterestAdsClient,
    parent_id: str,
    keywords: list[dict[str, Any]],
    *,
    ad_account_id: str | None,
    op: str,
    ctx: Context | None,
) -> dict[str, Any]:
    aid = client.resolve_ad_account(ad_account_id)
    path = client.account_path(aid, "keywords")
    results: list[Any] = []
    created: list[Any] = []
    errors: list[Any] = []
    total = len(keywords)
    for start in range(0, total, KEYWORD_BATCH):
        part = keywords[start : start + KEYWORD_BATCH]
        out = await run_blocking(
            client.request,
            "POST",
            path,
            json_body={"parent_id": parent_id, "keywords": part},
            ad_account_id=aid,
            op=op,
        )
        results.append(out)
        if isinstance(out, dict):
            created.extend(out.get("keywords") or [])
            errors.extend(out.get("errors") or [])
        done = min(start + KEYWORD_BATCH, total)
        await report(ctx, done, total, f"Sent {done} of {total} keywords")
    merged = merge_results(results, [])
    merged.pop("items", None)
    merged.pop("succeeded", None)
    merged.pop("with_errors", None)
    if not merged.get("dry_run"):
        merged.update({"keywords": created, "errors": errors, "created": len(created)})
    return merged


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_list_keywords(
        ad_group_id: str | None = None,
        campaign_id: str | None = None,
        ad_group_ids: list[str] | None = None,
        match_types: list[str] | None = None,
        ad_account_id: str | None = None,
        max_results: int = 500,
    ) -> dict[str, Any]:
        """List keywords and negative keywords for an ad group, several ad groups or a campaign.

        match_types filters, e.g. ['EXACT_NEGATIVE', 'PHRASE_NEGATIVE'] for negatives only.
        Archived keywords have archived=true.
        """
        params: dict[str, Any] = {}
        if ad_group_id:
            params["ad_group_id"] = check_ids([ad_group_id], "ad_group_id")[0]
        if campaign_id:
            params["campaign_id"] = check_ids([campaign_id], "campaign_id")[0]
        if ad_group_ids:
            params["ad_group_ids"] = check_ids(ad_group_ids, "ad_group_ids", 250)
        if not params:
            raise ValueError("Pass ad_group_id, ad_group_ids or campaign_id.")
        if match_types:
            bad = {m.upper() for m in match_types} - POSITIVE - NEGATIVE
            if bad:
                raise ValueError(f"Unknown match types: {sorted(bad)}.")
            params["match_types"] = [m.upper() for m in match_types]
        items, truncated = client.paginate(
            client.account_path(ad_account_id, "keywords"), params, max_results=max_results
        )
        return {"keywords": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    async def pinterest_ads_create_keywords(
        ad_group_id: str,
        keywords: list[Any],
        match_type: str = "BROAD",
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Add targeting keywords to an ad group. Changes the live account.

        keywords: strings (use match_type, default BROAD) or objects like
        {"value": "linen dress", "match_type": "PHRASE"}. Match types: BROAD, PHRASE, EXACT.
        More than PINTEREST_ADS_BULK_THRESHOLD keywords need confirm='APPLY'.
        """
        parent = check_ids([ad_group_id], "ad_group_id")[0]
        objs = _keyword_objects(keywords, match_type, POSITIVE)
        decision = safety.assess(client.config, count=len(objs))
        refused = safety.refusal(
            client.config, decision, confirm, preview={"parent_id": parent, "keywords": objs}
        )
        if refused:
            return refused
        out = await _create(
            client, parent, objs, ad_account_id=ad_account_id, op="create_keywords", ctx=ctx
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    async def pinterest_ads_create_negative_keywords(
        parent_id: str,
        keywords: list[Any],
        match_type: str = "PHRASE_NEGATIVE",
        ad_account_id: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Add negative keywords so ads don't show on those searches. Changes the live account.

        parent_id: an ad group ID, a campaign ID (applies to every ad group in it) or the ad
        account ID (applies to the whole account). match_type: PHRASE_NEGATIVE (default) or
        EXACT_NEGATIVE. More than PINTEREST_ADS_BULK_THRESHOLD keywords need confirm='APPLY'.
        """
        parent = str(parent_id).strip()
        if not _PARENT_RE.match(parent):
            raise ValueError("parent_id must be an ad group, campaign or ad account ID.")
        objs = _keyword_objects(keywords, match_type, NEGATIVE)
        decision = safety.assess(client.config, count=len(objs))
        refused = safety.refusal(
            client.config, decision, confirm, preview={"parent_id": parent, "keywords": objs}
        )
        if refused:
            return refused
        out = await _create(
            client,
            parent,
            objs,
            ad_account_id=ad_account_id,
            op="create_negative_keywords",
            ctx=ctx,
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def pinterest_ads_archive_keywords(
        ids: list[str], ad_account_id: str | None = None, confirm: str = ""
    ) -> dict[str, Any]:
        """Archive keywords or negative keywords by ID (find IDs with pinterest_ads_list_keywords).

        Archived keywords stop applying and can't be restored, so this needs
        confirm='ARCHIVE' after the user agrees. Up to 1000 IDs per call.
        """
        id_list = check_ids(ids, "ids", MAX_KEYWORDS)
        if not id_list:
            raise ValueError("Pass at least one keyword ID.")
        decision = safety.assess(client.config, count=len(id_list), destructive=safety.ARCHIVE)
        body = {"keywords": [{"id": i, "archived": True} for i in id_list]}
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        aid = client.resolve_ad_account(ad_account_id)
        out = client.request(
            "PATCH",
            client.account_path(aid, "keywords"),
            json_body=body,
            ad_account_id=aid,
            op="archive_keywords",
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def pinterest_ads_get_keyword_metrics(
        keywords: list[str], country_code: str, ad_account_id: str | None = None
    ) -> dict[str, Any]:
        """Monthly search volume on Pinterest for keywords in one country (e.g. 'GB', 'US').

        Up to 2000 keywords. Aggregated across Pinterest; use it to size keyword ideas.
        """
        clean = [k.strip() for k in keywords if k and k.strip()]
        if not clean or len(clean) > 2000:
            raise ValueError("Pass 1 to 2000 keywords.")
        if len(country_code.strip()) != 2:
            raise ValueError("country_code must be a two-letter code such as GB or US.")
        return client.request(
            "GET",
            client.account_path(ad_account_id, "keywords", "metrics"),
            params={"country_code": country_code.strip().upper(), "keywords": ",".join(clean)},
        )

    @mcp.tool()
    def pinterest_ads_suggest_keywords(term: str, limit: int = 10) -> dict[str, Any]:
        """Keyword ideas for a seed term: popular suggested terms and related terms."""
        seed = term.strip()
        if not seed:
            raise ValueError("Pass a term.")
        suggested = client.request(
            "GET", "/terms/suggested", params={"term": seed, "limit": max(1, min(limit, 10))}
        )
        related = client.request("GET", "/terms/related", params={"terms": [seed]})
        return {"term": seed, "suggested": suggested, "related": related}
