from __future__ import annotations

import dataclasses

from pinterest_ads_mcp import safety
from pinterest_ads_mcp.config import Config

M = safety.MICRO


def test_small_write_without_money_needs_nothing(config: Config) -> None:
    d = safety.assess(config, count=3)
    assert d.required is None
    assert safety.refusal(config, d, "") is None


def test_archive_and_delete_words(config: Config) -> None:
    d = safety.assess(config, count=1, destructive=safety.ARCHIVE)
    assert d.required == "ARCHIVE"
    refused = safety.refusal(config, d, "APPLY")
    assert refused is not None and refused["refused"] is True
    assert safety.refusal(config, d, "ARCHIVE") is None
    assert safety.refusal(config, d, "archive") is not None  # case-sensitive
    assert safety.assess(config, count=1, destructive=safety.DELETE).required == "DELETE"


def test_bulk_needs_apply(config: Config) -> None:
    assert safety.assess(config, count=config.bulk_threshold).required is None
    assert safety.assess(config, count=config.bulk_threshold + 1).required == "APPLY"


def test_always_apply(config: Config) -> None:
    d = safety.assess(config, count=1, always=safety.APPLY, reason="raw write")
    assert d.required == "APPLY" and d.reasons == ["raw write"]


def test_increase_above_limit_needs_apply(config: Config) -> None:
    small = safety.assess(
        config, count=1, changes=[safety.MoneyChange("1", "bid", "b", 1 * M, int(1.2 * M))]
    )
    assert small.required is None
    big = safety.assess(
        config, count=1, changes=[safety.MoneyChange("1", "bid", "b", 1 * M, int(1.5 * M))]
    )
    assert big.required == "APPLY"
    assert big.changes[0]["change_pct"] == 50.0
    assert big.changes[0]["old"] == 1.0 and big.changes[0]["new"] == 1.5
    decrease = safety.assess(
        config, count=1, changes=[safety.MoneyChange("1", "bid", "b", 1 * M, int(0.2 * M))]
    )
    assert decrease.required is None


def test_rise_from_zero_and_unknown_values_need_apply(config: Config) -> None:
    from_zero = safety.assess(
        config, count=1, changes=[safety.MoneyChange("1", "budget", "b", 0, 5 * M)]
    )
    assert from_zero.required == "APPLY"
    unknown = safety.assess(
        config, count=1, changes=[safety.MoneyChange("1", "budget", "b", None, 5 * M)]
    )
    assert unknown.required == "APPLY"


def test_caps_are_in_currency_units(config: Config) -> None:
    capped = dataclasses.replace(config, max_budget=50.0, max_bid=2.0)
    over = safety.assess(
        capped,
        count=1,
        changes=[safety.MoneyChange("new[0]", "budget", "daily_spend_cap", None, 80 * M)],
        creating=True,
    )
    assert over.required == "APPLY"
    ok = safety.assess(
        capped,
        count=1,
        changes=[safety.MoneyChange("new[0]", "budget", "daily_spend_cap", None, 20 * M)],
        creating=True,
    )
    assert ok.required is None
    bid = safety.assess(
        capped, count=1, changes=[safety.MoneyChange("1", "bid", "b", 2 * M, int(2.1 * M))]
    )
    assert any("cap" in r for r in bid.reasons)


def test_dry_run_never_refuses(config: Config) -> None:
    d = safety.assess(config, count=1, destructive=safety.ARCHIVE)
    assert safety.refusal(dataclasses.replace(config, dry_run=True), d, "") is None


def test_money_changes_pairs_with_current() -> None:
    changes = safety.money_changes(
        [{"id": "1", "daily_spend_cap": 30 * M}, {"id": "2", "status": "PAUSED"}],
        [{"id": "1", "daily_spend_cap": 20 * M}],
        fields=(("budget", "daily_spend_cap"),),
    )
    assert changes == [safety.MoneyChange("1", "budget", "daily_spend_cap", 20 * M, 30 * M)]


def test_helpers() -> None:
    assert safety.to_micro(1.25) == 1_250_000
    assert safety.from_micro(2_500_000) == 2.5
    assert safety.archives_in([{"status": "archived"}, {"status": "PAUSED"}, {}]) == 1
