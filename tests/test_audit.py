from __future__ import annotations

import time
from pathlib import Path

from pinterest_ads_mcp.audit import AuditLog

from .conftest import audit_entries


def test_record_redacts_tokens(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(
        op="x",
        resource="/x",
        method="PATCH",
        url="u",
        request_body={"note": "pina_secrettokenvalue", "access_token": "abc"},
    )
    [entry] = audit_entries(log.path)
    assert entry["request_body"] == {"note": "***", "access_token": "***"}


def test_find_and_tail(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    ids = [log.record(op=f"op{i}", resource="/r", method="POST", url="u") for i in range(3)]
    found = log.find(ids[1])
    assert found is not None and found["op"] == "op1"
    assert [e["op"] for e in log.tail(2)] == ["op1", "op2"]
    assert log.tail(0) == []
    assert log.find("nope") is None


def test_prune_drops_entries_past_retention(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl", retention_days=30)
    log.record(op="old", resource="/r", method="POST", url="u")
    log.record(op="old2", resource="/r", method="POST", url="u")
    assert log.prune(now=time.time() + 31 * 86400) == 2
    assert audit_entries(log.path) == []
    log.record(op="fresh", resource="/r", method="POST", url="u")
    assert log.prune() == 0
    assert [e["op"] for e in audit_entries(log.path)] == ["fresh"]


def test_zero_retention_keeps_everything(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl", retention_days=0)
    log.record(op="x", resource="/r", method="POST", url="u")
    assert log.prune(now=time.time() + 10_000 * 86400) == 0


def test_purge(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(op="x", resource="/r", method="POST", url="u")
    assert log.purge() is True
    assert not log.path.exists()
    assert log.purge() is False


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(op="x", resource="/r", method="POST", url="u")
    with log.path.open("a") as f:
        f.write("not json\n")
    assert len(log.entries()) == 1
