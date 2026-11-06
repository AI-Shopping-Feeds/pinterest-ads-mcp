"""Targeting options, targeting templates, audience sizing, audiences and customer lists.

Customer-list identifiers are normalised (trimmed, lower-cased) and SHA-256
hashed on this machine before they are sent, unless they already look hashed.
The audit log records only how many identifiers were sent, never the values.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .. import safety
from ..client import PinterestAdsClient, segment

TARGETING_TYPES = {
    "APPTYPE",
    "GENDER",
    "LOCALE",
    "AGE_BUCKET",
    "LOCATION",
    "GEO",
    "INTEREST",
    "KEYWORD",
    "AUDIENCE_INCLUDE",
    "AUDIENCE_EXCLUDE",
}
AUDIENCE_TYPES = {"CUSTOMER_LIST", "VISITOR", "ENGAGEMENT", "ACTALIKE", "PERSONA"}
LIST_TYPES = {"EMAIL", "IDFA", "MAID", "LR_ID", "DLX_ID", "HASHED_PINNER_ID"}
HASHABLE = {"EMAIL", "IDFA", "MAID"}
MAX_IDENTIFIERS = 50_000
_HASH_RE = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")


def hash_identifier(value: str) -> str:
    """SHA-256 of the trimmed, lower-cased value, unless it already looks like an MD5/SHA hash."""
    norm = value.strip().lower()
    if _HASH_RE.match(norm):
        return norm
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def prepare_records(identifiers: list[str], list_type: str | None, hash_values: bool) -> str:
    clean = [str(v).strip() for v in identifiers if str(v).strip()]
    if not clean:
        raise ValueError("Pass at least one identifier.")
    if len(clean) > MAX_IDENTIFIERS:
        raise ValueError(
            f"At most {MAX_IDENTIFIERS} identifiers per call. Split the list, or use Pinterest's "
            "asynchronous customer list upload through pinterest_ads_request."
        )
    if any("," in v for v in clean):
        raise ValueError("Identifiers can't contain commas.")
    if hash_values and (list_type is None or list_type in HASHABLE):
        clean = [hash_identifier(v) for v in clean]
    return ",".join(clean)


def _filter_options(data: Any, search: str | None, max_results: int) -> Any:
    """Trim a (possibly huge) targeting options response to matches for `search`."""
    needle = (search or "").strip().lower()
    if isinstance(data, dict):
        pairs = [(k, v) for k, v in data.items() if not needle or needle in f"{k} {v}".lower()]
        return {"options": dict(pairs[:max_results]), "count": len(pairs)}
    if isinstance(data, list):
        rows = [r for r in data if not needle or needle in str(r).lower()]
        return {"options": rows[:max_results], "count": len(rows)}
    return data


def register(mcp, client: PinterestAdsClient) -> None:
    @mcp.tool()
    def pinterest_ads_get_targeting_options(
        targeting_type: str,
        search: str | None = None,
        max_results: int = 200,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Valid values for an ad group targeting_spec field.

        targeting_type: LOCATION (countries and US metros), GEO (regions, postcodes), INTEREST,
        AGE_BUCKET, GENDER, LOCALE, APPTYPE, KEYWORD, AUDIENCE_INCLUDE or AUDIENCE_EXCLUDE.
        search filters the (often long) list, e.g. search='united kingdom'.
        """
        kind = targeting_type.strip().upper()
        if kind not in TARGETING_TYPES:
            raise ValueError(f"targeting_type must be one of {', '.join(sorted(TARGETING_TYPES))}.")
        params = {"ad_account_id": segment(ad_account_id, "ad_account_id")} if ad_account_id else {}
        data = client.request("GET", f"/resources/targeting/{kind}", params=params)
        return {"targeting_type": kind, **_filter_options(data, search, max_results)}

    @mcp.tool()
    def pinterest_ads_list_targeting_templates(
        ad_account_id: str | None = None,
        search_query: str | None = None,
        include_sizing: bool = False,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """List saved targeting templates (reusable targeting for ad groups)."""
        params: dict[str, Any] = {"include_sizing": str(include_sizing).lower()}
        if search_query:
            params["search_query"] = search_query
        items, truncated = client.paginate(
            client.account_path(ad_account_id, "targeting_templates"),
            params,
            max_results=max_results,
        )
        return {"targeting_templates": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_estimate_audience_size(
        targeting_spec: dict[str, Any],
        keywords: list[dict[str, Any]] | None = None,
        auto_targeting_enabled: bool | None = None,
        creative_types: list[str] | None = None,
        product_group_ids: list[str] | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Estimate how many people an ad group's targeting would reach. Changes nothing.

        targeting_spec as in ad group create, e.g. {"LOCATION": ["GB"], "AGE_BUCKET":
        ["25-34"]}. keywords: [{"value": "linen dress", "match_type": "BROAD"}].
        """
        body: dict[str, Any] = {"targeting_spec": targeting_spec}
        if keywords:
            body["keywords"] = keywords
        if auto_targeting_enabled is not None:
            body["auto_targeting_enabled"] = auto_targeting_enabled
        if creative_types:
            body["creative_types"] = [c.upper() for c in creative_types]
        if product_group_ids:
            body["product_group_ids"] = product_group_ids
        return client.request(
            "POST",
            client.account_path(ad_account_id, "ad_groups", "audience_sizing"),
            json_body=body,
            read_only=True,
        )

    @mcp.tool()
    def pinterest_ads_list_audiences(
        ad_account_id: str | None = None,
        ownership_type: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List audiences (customer list, visitor, engagement, actalike and persona audiences).

        ownership_type: OWNED or RECEIVED (shared with this account). Audiences need at least
        100 matched people before they can be used.
        """
        params: dict[str, Any] = {}
        if ownership_type:
            params["ownership_type"] = ownership_type.upper()
        items, truncated = client.paginate(
            client.account_path(ad_account_id, "audiences"), params, max_results=max_results
        )
        return {"audiences": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_get_audience(
        audience_id: str, ad_account_id: str | None = None
    ) -> dict[str, Any]:
        """Get one audience: type, rule, size and status."""
        return client.request(
            "GET",
            client.account_path(ad_account_id, "audiences", segment(audience_id, "audience_id")),
        )

    @mcp.tool()
    def pinterest_ads_create_audience(
        name: str,
        audience_type: str,
        rule: dict[str, Any],
        description: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an audience for targeting. Changes the live account.

        audience_type and rule, for example:
        - CUSTOMER_LIST: {"customer_list_id": "..."}
        - VISITOR: {"visitor_source_id": "<conversion tag id>", "retention_days": 30,
          "event": "checkout"}
        - ENGAGEMENT: {"engager_type": 1, "retention_days": 180}
        - ACTALIKE: {"seed_id": ["<audience id>"], "country": "GB", "percentage": 5}
        Use only data you have the rights and consents to use under Pinterest's Ad Data Terms.
        """
        kind = audience_type.strip().upper()
        if kind not in AUDIENCE_TYPES:
            raise ValueError(f"audience_type must be one of {', '.join(sorted(AUDIENCE_TYPES))}.")
        aid = client.resolve_ad_account(ad_account_id)
        body: dict[str, Any] = {
            "ad_account_id": aid,
            "name": name,
            "audience_type": kind,
            "rule": rule,
        }
        if description:
            body["description"] = description
        return client.request(
            "POST",
            client.account_path(aid, "audiences"),
            json_body=body,
            ad_account_id=aid,
            op="create_audience",
        )

    @mcp.tool()
    def pinterest_ads_update_audience(
        audience_id: str,
        name: str | None = None,
        description: str | None = None,
        rule: dict[str, Any] | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Rename an audience or change its description or rule. Changes the live account."""
        aid = client.resolve_ad_account(ad_account_id)
        path = client.account_path(aid, "audiences", segment(audience_id, "audience_id"))
        body: dict[str, Any] = {"ad_account_id": aid, "operation_type": "UPDATE"}
        for key, value in (("name", name), ("description", description), ("rule", rule)):
            if value is not None:
                body[key] = value
        if len(body) == 2:
            raise ValueError("Pass name, description or rule to change.")
        current = client.snapshot(client.request, "GET", path)
        before = (
            {k: current.get(k) for k in ("id", "name", "description", "rule") if k in current}
            if isinstance(current, dict)
            else None
        )
        return client.request(
            "PATCH", path, json_body=body, ad_account_id=aid, op="update_audience", before=before
        )

    @mcp.tool()
    def pinterest_ads_delete_audience(
        audience_id: str, ad_account_id: str | None = None, confirm: str = ""
    ) -> dict[str, Any]:
        """Remove an audience. Ad groups targeting it lose that targeting, and it can't be
        undone, so this needs confirm='DELETE' after the user agrees."""
        aid = client.resolve_ad_account(ad_account_id)
        path = client.account_path(aid, "audiences", segment(audience_id, "audience_id"))
        decision = safety.assess(client.config, count=1, destructive=safety.DELETE)
        body = {"ad_account_id": aid, "operation_type": "REMOVE"}
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        current = client.snapshot(client.request, "GET", path)
        before = (
            {k: current.get(k) for k in ("id", "name", "audience_type") if k in current}
            if isinstance(current, dict)
            else None
        )
        out = client.request(
            "PATCH", path, json_body=body, ad_account_id=aid, op="delete_audience", before=before
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    def pinterest_ads_list_customer_lists(
        ad_account_id: str | None = None, max_results: int = 200
    ) -> dict[str, Any]:
        """List customer lists (uploaded customer identifiers) with their size and status.

        Status TOO_SMALL means fewer than 100 matched Pinterest users.
        """
        items, truncated = client.paginate(
            client.account_path(ad_account_id, "customer_lists"), {}, max_results=max_results
        )
        return {"customer_lists": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def pinterest_ads_create_customer_list(
        name: str,
        identifiers: list[str],
        list_type: str = "EMAIL",
        hash_identifiers: bool = True,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload a customer list (emails or mobile ad IDs) for targeting. Changes the account.

        list_type: EMAIL (default), MAID, IDFA, LR_ID, DLX_ID or HASHED_PINNER_ID. Emails and
        ad IDs are trimmed, lower-cased and SHA-256 hashed here before sending (unless already
        hashed). Up to 50,000 identifiers per call. Only upload people who agreed to this use;
        then turn the list into an audience with pinterest_ads_create_audience.
        """
        kind = list_type.strip().upper()
        if kind not in LIST_TYPES:
            raise ValueError(f"list_type must be one of {', '.join(sorted(LIST_TYPES))}.")
        records = prepare_records(identifiers, kind, hash_identifiers)
        count = records.count(",") + 1
        aid = client.resolve_ad_account(ad_account_id)
        return client.request(
            "POST",
            client.account_path(aid, "customer_lists"),
            json_body={"name": name, "records": records, "list_type": kind},
            ad_account_id=aid,
            op="create_customer_list",
            audit_body={"name": name, "list_type": kind, "records": f"<{count} identifiers>"},
        )

    @mcp.tool()
    def pinterest_ads_update_customer_list(
        customer_list_id: str,
        identifiers: list[str],
        operation: str = "ADD",
        hash_identifiers: bool = True,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Add people to, or remove people from, a customer list. Changes the account.

        operation: ADD or REMOVE. Identifiers are hashed as in pinterest_ads_create_customer_list.
        Up to 50,000 per call.
        """
        op = operation.strip().upper()
        if op not in {"ADD", "REMOVE"}:
            raise ValueError("operation must be ADD or REMOVE.")
        records = prepare_records(identifiers, None, hash_identifiers)
        count = records.count(",") + 1
        aid = client.resolve_ad_account(ad_account_id)
        return client.request(
            "PATCH",
            client.account_path(
                aid, "customer_lists", segment(customer_list_id, "customer_list_id")
            ),
            json_body={"operation_type": op, "records": records},
            ad_account_id=aid,
            op=f"customer_list_{op.lower()}",
            audit_body={"operation_type": op, "records": f"<{count} identifiers>"},
        )
