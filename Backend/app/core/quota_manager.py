import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Limits are env-driven via settings (see config.py: GEMINI_RPM_CAP, GEMINI_DAILY_CAP_ENABLED, GEMINI_RPD_CAP)


class DailyQuotaExceededError(Exception):
    pass


class QuotaManager:
    """
    Atomic quota tracker backed by Supabase PostgreSQL.

    Uses SELECT FOR UPDATE row locking so concurrent requests from
    multiple users or batch jobs cannot both pass the quota check
    simultaneously — one waits for the other's transaction to commit
    before reading the incremented counter.
    """

    def _window_keys(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "date_key": now.strftime("%Y-%m-%d"),
            "minute_key": now.strftime("%Y-%m-%d %H:%M"),
        }

    async def _try_reserve(self, model: str, keys: dict) -> tuple[bool, dict]:
        """
        One atomic transaction:
        1. INSERT rows if they don't exist (ON CONFLICT DO NOTHING)
        2. Lock both rows with FOR UPDATE — blocks any other concurrent transaction
        3. Read current counts
        4. If within limits: increment and commit
        5. If over limit: rollback (no increment)
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Ensure rows exist without touching counts
                await session.execute(text("""
                    INSERT INTO gemini_quota (id, model, window_type, window_key, request_count, updated_at)
                    VALUES
                        (gen_random_uuid(), :model, 'daily',  :date_key,   0, NOW()),
                        (gen_random_uuid(), :model, 'minute', :minute_key, 0, NOW())
                    ON CONFLICT (model, window_type, window_key) DO NOTHING
                """), {"model": model, **keys})

                # Lock rows — concurrent requests block here until we commit
                result = await session.execute(text("""
                    SELECT window_type, request_count
                    FROM gemini_quota
                    WHERE model = :model
                      AND (
                            (window_type = 'daily'  AND window_key = :date_key)
                         OR (window_type = 'minute' AND window_key = :minute_key)
                          )
                    FOR UPDATE
                """), {"model": model, **keys})

                rows = result.fetchall()
                daily = next((r.request_count for r in rows if r.window_type == "daily"), 0)
                minute = next((r.request_count for r in rows if r.window_type == "minute"), 0)

                daily_exceeded = settings.GEMINI_DAILY_CAP_ENABLED and daily >= settings.GEMINI_RPD_CAP
                if daily_exceeded or minute >= settings.GEMINI_RPM_CAP:
                    # Transaction rolls back — no increment
                    return False, {"daily": daily, "minute": minute}

                # Atomically increment both windows
                await session.execute(text("""
                    UPDATE gemini_quota
                    SET request_count = request_count + 1, updated_at = NOW()
                    WHERE model = :model
                      AND (
                            (window_type = 'daily'  AND window_key = :date_key)
                         OR (window_type = 'minute' AND window_key = :minute_key)
                          )
                """), {"model": model, **keys})

                return True, {"daily": daily + 1, "minute": minute + 1}

    async def check_and_reserve(self, model: str) -> dict:
        """
        Acquire a quota slot before calling Gemini.

        - If RPM limit hit: waits until the next minute window, then retries.
        - If RPD limit hit: raises DailyQuotaExceededError immediately.
        - Returns usage dict on success.
        """
        for _ in range(120):  # max 2 hours of waiting
            keys = self._window_keys()
            allowed, usage = await self._try_reserve(model, keys)

            if allowed:
                logger.debug(
                    f"Quota reserved — daily: {usage['daily']}/{settings.GEMINI_RPD_CAP}, "
                    f"minute: {usage['minute']}/{settings.GEMINI_RPM_CAP}"
                )
                return usage

            if settings.GEMINI_DAILY_CAP_ENABLED and usage["daily"] >= settings.GEMINI_RPD_CAP:
                msg = f"Daily quota limit reached ({usage['daily']}/{settings.GEMINI_RPD_CAP}). No more Gemini calls today."
                logger.error(msg)
                raise DailyQuotaExceededError(msg)

            # Only RPM exceeded — wait for next minute window
            now = datetime.now(timezone.utc)
            wait_secs = 61 - now.second  # +1 for clock skew safety
            logger.warning(
                f"RPM limit hit ({usage['minute']}/{settings.GEMINI_RPM_CAP}). "
                f"Waiting {wait_secs}s for next minute window..."
            )
            await asyncio.sleep(wait_secs)

        raise DailyQuotaExceededError("Could not acquire quota slot after extended waiting")

    async def get_usage(self, model: str) -> dict:
        """Return current usage stats for the dashboard/API."""
        keys = self._window_keys()
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("""
                SELECT window_type, request_count
                FROM gemini_quota
                WHERE model = :model
                  AND (
                        (window_type = 'daily'  AND window_key = :date_key)
                     OR (window_type = 'minute' AND window_key = :minute_key)
                      )
            """), {"model": model, **keys})
            rows = result.fetchall()

        daily = next((r.request_count for r in rows if r.window_type == "daily"), 0)
        minute = next((r.request_count for r in rows if r.window_type == "minute"), 0)

        daily_cap = settings.GEMINI_RPD_CAP
        rpm_cap = settings.GEMINI_RPM_CAP

        return {
            "model": model,
            "daily": {
                "used": daily,
                "limit": daily_cap if settings.GEMINI_DAILY_CAP_ENABLED else None,
                "remaining": max(0, daily_cap - daily) if settings.GEMINI_DAILY_CAP_ENABLED else None,
                "percent_used": round(daily / daily_cap * 100, 1) if settings.GEMINI_DAILY_CAP_ENABLED else 0.0,
            },
            "per_minute": {
                "used": minute,
                "limit": rpm_cap,
                "remaining": max(0, rpm_cap - minute),
                "percent_used": round(minute / rpm_cap * 100, 1),
            },
            "within_free_tier": (not settings.GEMINI_DAILY_CAP_ENABLED or daily < daily_cap) and minute < rpm_cap,
        }

    async def cleanup_old_windows(self):
        """Delete minute-window rows older than 2 hours to keep the table small."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("""
                DELETE FROM gemini_quota
                WHERE window_type = 'minute'
                  AND updated_at < NOW() - INTERVAL '2 hours'
            """))
            await session.commit()
            logger.info(f"Quota cleanup: removed {result.rowcount} old minute-window rows")


quota_manager = QuotaManager()
