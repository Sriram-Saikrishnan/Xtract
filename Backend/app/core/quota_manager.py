import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Free tier limits for Tier 1 (billing enabled)
# Actual limits: 10 RPM, 500 RPD for gemini-2.5-flash
# We enforce 90% of each to maintain a safety buffer
FREE_TIER_RPM = 9    # actual: 10
FREE_TIER_RPD = 450  # actual: 500


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

                if daily >= FREE_TIER_RPD or minute >= FREE_TIER_RPM:
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
                    f"Quota reserved — daily: {usage['daily']}/{FREE_TIER_RPD}, "
                    f"minute: {usage['minute']}/{FREE_TIER_RPM}"
                )
                return usage

            if usage["daily"] >= FREE_TIER_RPD:
                msg = f"Daily free tier limit reached ({usage['daily']}/{FREE_TIER_RPD}). No more Gemini calls today."
                logger.error(msg)
                raise DailyQuotaExceededError(msg)

            # Only RPM exceeded — wait for next minute window
            now = datetime.now(timezone.utc)
            wait_secs = 61 - now.second  # +1 for clock skew safety
            logger.warning(
                f"RPM limit hit ({usage['minute']}/{FREE_TIER_RPM}). "
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

        return {
            "model": model,
            "daily": {
                "used": daily,
                "limit": FREE_TIER_RPD,
                "remaining": max(0, FREE_TIER_RPD - daily),
                "percent_used": round(daily / FREE_TIER_RPD * 100, 1),
            },
            "per_minute": {
                "used": minute,
                "limit": FREE_TIER_RPM,
                "remaining": max(0, FREE_TIER_RPM - minute),
                "percent_used": round(minute / FREE_TIER_RPM * 100, 1),
            },
            "within_free_tier": daily < FREE_TIER_RPD and minute < FREE_TIER_RPM,
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
