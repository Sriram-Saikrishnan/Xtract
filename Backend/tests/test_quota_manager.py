"""
Step 1 — quota_manager respects settings-driven limits instead of the old
hardcoded FREE_TIER_RPM/RPD constants, and the daily cap is fully skippable.

quota_manager.py talks to Postgres via raw text() SQL using Postgres-only
syntax (FOR UPDATE row locking, ON CONFLICT, gen_random_uuid()) that SQLite
cannot run. Real DB execution is out for these tests; instead a small fake
session intercepts and records every SQL statement + params, applying the
same semantics a real Postgres backend would (insert-if-missing, read
counts, conditionally increment) — letting us assert on both behavior and
the literal SQL text emitted (to prove the FOR UPDATE clause is still there).
"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.quota_manager import DailyQuotaExceededError, QuotaManager


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeQuotaSession:
    """Records every SQL statement and simulates gemini_quota table semantics."""

    def __init__(self, store):
        self.store = store  # dict[(model, window_type, window_key)] -> count
        self.executed_sql = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _NoopAsyncCM()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed_sql.append(sql)
        params = params or {}
        model = params.get("model")

        if "INSERT INTO gemini_quota" in sql:
            for wt, wk in (("daily", params.get("date_key")), ("minute", params.get("minute_key"))):
                self.store.setdefault((model, wt, wk), 0)
            return None

        if sql.strip().startswith("SELECT window_type"):
            rows = []
            for wt, wk in (("daily", params.get("date_key")), ("minute", params.get("minute_key"))):
                rows.append(SimpleNamespace(window_type=wt, request_count=self.store.get((model, wt, wk), 0)))
            return FakeResult(rows)

        if sql.strip().startswith("UPDATE gemini_quota"):
            for wt, wk in (("daily", params.get("date_key")), ("minute", params.get("minute_key"))):
                key = (model, wt, wk)
                self.store[key] = self.store.get(key, 0) + 1
            return None

        raise AssertionError(f"Unexpected SQL in fake quota session: {sql}")


class _NoopAsyncCM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def quota_store():
    return {}


@pytest.fixture
def patched_quota_manager(monkeypatch, quota_store):
    """Patch quota_manager.AsyncSessionLocal with a factory returning FakeQuotaSession."""
    import app.core.quota_manager as qm

    def factory():
        return FakeQuotaSession(quota_store)

    monkeypatch.setattr(qm, "AsyncSessionLocal", factory)
    return qm


async def test_rpm_cap_comes_from_settings_not_hardcoded(monkeypatch, patched_quota_manager, quota_store):
    """Old code hardcoded FREE_TIER_RPM=9; confirm the limit now tracks settings.GEMINI_RPM_CAP."""
    monkeypatch.setattr(settings, "GEMINI_RPM_CAP", 2)
    monkeypatch.setattr(settings, "GEMINI_DAILY_CAP_ENABLED", False)

    manager = QuotaManager()

    usage1 = await manager.check_and_reserve("gemini-test")
    usage2 = await manager.check_and_reserve("gemini-test")
    assert usage1["minute"] == 1
    assert usage2["minute"] == 2

    # Third reservation should NOT pass instantly — RPM cap of 2 is hit.
    # _try_reserve directly (bypassing the 61s sleep loop in check_and_reserve)
    # confirms the comparison uses settings.GEMINI_RPM_CAP=2, not the old hardcoded 9.
    keys = manager._window_keys()
    allowed, usage = await manager._try_reserve("gemini-test", keys)
    assert allowed is False
    assert usage["minute"] == 2


async def test_rpm_cap_of_9_would_have_blocked_under_old_hardcoded_value(monkeypatch, patched_quota_manager):
    """With the new Tier-1 default (3600), 9 rapid reservations must all succeed —
    proving the old FREE_TIER_RPM=9 ceiling is gone."""
    monkeypatch.setattr(settings, "GEMINI_RPM_CAP", 3600)
    monkeypatch.setattr(settings, "GEMINI_DAILY_CAP_ENABLED", False)

    manager = QuotaManager()
    for _ in range(9):
        usage = await manager.check_and_reserve("gemini-test")
    assert usage["minute"] == 9  # all 9 passed — would have failed at >=9 under the old free-tier cap


async def test_daily_cap_skipped_when_disabled(monkeypatch, patched_quota_manager, quota_store):
    monkeypatch.setattr(settings, "GEMINI_DAILY_CAP_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_RPD_CAP", 1)  # would be exceeded immediately if enforced
    monkeypatch.setattr(settings, "GEMINI_RPM_CAP", 3600)

    manager = QuotaManager()
    # Reserve well past the (disabled) daily cap of 1 — must not raise.
    for _ in range(5):
        usage = await manager.check_and_reserve("gemini-test")
    assert usage["daily"] == 5


async def test_daily_cap_enforced_when_enabled(monkeypatch, patched_quota_manager, quota_store):
    monkeypatch.setattr(settings, "GEMINI_DAILY_CAP_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_RPD_CAP", 2)
    monkeypatch.setattr(settings, "GEMINI_RPM_CAP", 3600)

    manager = QuotaManager()
    await manager.check_and_reserve("gemini-test")
    await manager.check_and_reserve("gemini-test")

    with pytest.raises(DailyQuotaExceededError):
        await manager.check_and_reserve("gemini-test")


async def test_row_locking_sql_still_present(monkeypatch, patched_quota_manager, quota_store):
    """Confirm the SELECT...FOR UPDATE row-locking statement is still emitted —
    Step 1 was only supposed to change the numeric limits, not this logic."""
    monkeypatch.setattr(settings, "GEMINI_RPM_CAP", 3600)
    monkeypatch.setattr(settings, "GEMINI_DAILY_CAP_ENABLED", False)

    manager = QuotaManager()
    fake_session = FakeQuotaSession(quota_store)

    import app.core.quota_manager as qm
    monkeypatch.setattr(qm, "AsyncSessionLocal", lambda: fake_session)

    await manager.check_and_reserve("gemini-test")

    locking_statements = [sql for sql in fake_session.executed_sql if "FOR UPDATE" in sql]
    assert locking_statements, "Expected a SELECT ... FOR UPDATE statement to be executed"
    assert "ON CONFLICT" in "".join(fake_session.executed_sql)
