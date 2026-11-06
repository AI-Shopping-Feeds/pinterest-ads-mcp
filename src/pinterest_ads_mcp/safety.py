"""Confirmation guards for writes that change live ads or spend money.

A write tool asks `assess()` what its change needs, then `refusal()` whether to
stop. A guarded write is refused (with a summary of what it would do) unless the
caller passes the required `confirm` word:

* archives (status ARCHIVED, which Pinterest can't undo) need confirm='ARCHIVE';
* deletes (product groups, catalog items, audiences) need confirm='DELETE';
* writes touching more than PINTEREST_ADS_BULK_THRESHOLD entities, bid or
  budget increases above PINTEREST_ADS_MAX_INCREASE_PCT, increases whose current
  value couldn't be read, values over PINTEREST_ADS_MAX_BUDGET /
  PINTEREST_ADS_MAX_BID, bulk upserts and raw writes need confirm='APPLY'.

Pinterest money fields are in micro-units of the ad account's currency
(1.00 = 1,000,000). Caps are set in whole currency units.

In dry-run mode nothing is sent, so guards don't block; the result says which
confirm a real run would need.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import Config

APPLY = "APPLY"
ARCHIVE = "ARCHIVE"
DELETE = "DELETE"
MICRO = 1_000_000


@dataclass
class MoneyChange:
    """A bid or budget an update sets, in micro-units. `old` is None if it couldn't be read."""

    entity_id: str
    kind: str  # "bid" or "budget"
    field: str
    old: int | None
    new: int


@dataclass
class Decision:
    required: str | None = None
    reasons: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_confirm": self.required,
            "reasons": self.reasons,
            "money_changes": self.changes[:50],
        }


def to_micro(amount: float) -> int:
    return round(float(amount) * MICRO)


def from_micro(value: int | None) -> float | None:
    return None if value is None else round(value / MICRO, 6)


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def money_changes(
    items: Iterable[dict[str, Any]],
    current: Iterable[dict[str, Any]] | None,
    *,
    fields: Sequence[tuple[str, str]],
    id_field: str = "id",
) -> list[MoneyChange]:
    """Pair each update's bid/budget fields with the entity's current values.

    fields: (kind, field name) pairs, e.g. ("budget", "daily_spend_cap").
    """
    by_id = {str(c.get(id_field)): c for c in (current or []) if isinstance(c, dict)}
    out: list[MoneyChange] = []
    for item in items:
        eid = str(item.get(id_field))
        for kind, name in fields:
            if name not in item:
                continue
            new = _int(item.get(name))
            if new is None:
                continue
            old = _int(by_id[eid].get(name)) if eid in by_id else None
            out.append(MoneyChange(eid, kind, name, old, new))
    return out


def new_values(
    items: Iterable[dict[str, Any]], fields: Sequence[tuple[str, str]]
) -> list[MoneyChange]:
    """Bid/budget values set by creates (no previous value to compare)."""
    out: list[MoneyChange] = []
    for i, item in enumerate(items):
        for kind, name in fields:
            new = _int(item.get(name))
            if new is not None:
                out.append(MoneyChange(f"new[{i}]", kind, name, None, new))
    return out


def assess(
    config: Config,
    *,
    count: int,
    destructive: str | None = None,
    changes: Sequence[MoneyChange] = (),
    creating: bool = False,
    always: str | None = None,
    reason: str | None = None,
) -> Decision:
    """Work out which confirm word, if any, a write needs.

    always: a confirm word this kind of write always needs (e.g. APPLY for raw
    writes), with `reason` explaining why.
    """
    d = Decision()
    if destructive:
        d.required = destructive
        verb = "archive" if destructive == ARCHIVE else "delete"
        d.reasons.append(f"{verb} of {count} item(s) can't be undone")
    if always and d.required is None:
        d.required = always
    if reason:
        d.reasons.append(reason)
    if count > config.bulk_threshold:
        d.reasons.append(
            f"{count} items is more than PINTEREST_ADS_BULK_THRESHOLD ({config.bulk_threshold})"
        )
    limit = config.max_increase_pct
    for c in changes:
        info: dict[str, Any] = {
            "id": c.entity_id,
            "kind": c.kind,
            "field": c.field,
            "old_micro": c.old,
            "new_micro": c.new,
            "old": from_micro(c.old),
            "new": from_micro(c.new),
        }
        if c.old is not None and c.old > 0:
            info["change_pct"] = round((c.new - c.old) / c.old * 100, 1)
        d.changes.append(info)
        cap = config.max_budget if c.kind == "budget" else config.max_bid
        if cap is not None and c.new > to_micro(cap):
            d.reasons.append(
                f"{c.kind} {from_micro(c.new)} for {c.entity_id} is above the cap of {cap:g} "
                f"(PINTEREST_ADS_MAX_{c.kind.upper()})"
            )
        if creating:
            continue
        if c.old is None:
            d.reasons.append(
                f"current {c.field} for {c.entity_id} is unknown, so the change is unchecked"
            )
        elif c.old <= 0 < c.new or (c.old > 0 and (c.new - c.old) / c.old * 100 > limit):
            d.reasons.append(
                f"{c.field} for {c.entity_id} rises from {from_micro(c.old)} to "
                f"{from_micro(c.new)} (more than PINTEREST_ADS_MAX_INCREASE_PCT={limit:g}%)"
            )
    if d.reasons and d.required is None:
        d.required = APPLY
    return d


def refusal(
    config: Config, decision: Decision, confirm: str, *, preview: Any = None
) -> dict[str, Any] | None:
    """Return a refusal payload if the write must not go ahead, else None."""
    if decision.required is None or config.dry_run or confirm == decision.required:
        return None
    out: dict[str, Any] = {
        "refused": True,
        "error": (
            "Not sent. Show the user exactly what will change, and once they agree call "
            f"again with confirm='{decision.required}' (case-sensitive)."
        ),
        **decision.as_dict(),
    }
    if preview is not None:
        out["would_send"] = preview
    return out


def annotate(result: Any, decision: Decision) -> Any:
    """Attach the guard summary to a result (useful in dry-run)."""
    if decision.required is None:
        return result
    if isinstance(result, dict):
        return {**result, "safety": decision.as_dict()}
    return {"result": result, "safety": decision.as_dict()}


def archives_in(items: Iterable[dict[str, Any]]) -> int:
    """How many items set status ARCHIVED."""
    return sum(1 for i in items if str(i.get("status", "")).upper() == "ARCHIVED")
