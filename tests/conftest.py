"""Shared fixtures for the test suite.

All tests run offline: HTTP is mocked with respx and no real Pinterest
credentials are used. Every value below is a made-up placeholder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pinterest_ads_mcp.audit import AuditLog
from pinterest_ads_mcp.client import PinterestAdsClient
from pinterest_ads_mcp.config import Config

ACCOUNT = "123456789"
BASE = "https://api.pinterest.com/v5"
ACCOUNT_BASE = f"{BASE}/ad_accounts/{ACCOUNT}"
APP_ID = "1234567"


class FakeAuth:
    """Stand-in for RefreshTokenAuth in tests."""

    account_label = "fake:test"
    can_refresh = True

    def __init__(self) -> None:
        self.invalidated = 0
        self.token = "pina_fakeaccesstokenvalue"

    def access_token(self) -> str:
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1

    def status(self) -> dict[str, Any]:
        return {"type": "fake", "scopes": ["ads:read", "ads:write"], "refresh_token_days_left": 50}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No real env vars, .env files or home folder leak into tests."""
    for key in list(os.environ):
        if key.startswith("PINTEREST_ADS_"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("PINTEREST_ADS_HOME", str(home))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("pinterest_ads_mcp.client.time.sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def tmp_audit_log(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def config(tmp_audit_log: Path, tmp_path: Path) -> Config:
    return Config(
        app_id=APP_ID,
        app_secret="test-app-secret-value",
        refresh_token="pinr_fakerefreshtokenvalue",
        ad_account_id=ACCOUNT,
        audit_log=tmp_audit_log,
        token_file=tmp_path / "secrets" / "token.json",
        page_size=100,
        timeout=5.0,
        dry_run=False,
    )


@pytest.fixture
def audit(tmp_audit_log: Path) -> AuditLog:
    return AuditLog(tmp_audit_log)


@pytest.fixture
def fake_auth() -> FakeAuth:
    return FakeAuth()


@pytest.fixture
def client(config: Config, audit: AuditLog, fake_auth: FakeAuth):
    c = PinterestAdsClient(config, fake_auth, audit)
    yield c
    c.close()


def audit_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
