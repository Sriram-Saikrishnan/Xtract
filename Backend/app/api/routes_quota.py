from fastapi import APIRouter, Depends
from app.config import settings
from app.core.quota_manager import quota_manager, FREE_TIER_RPM, FREE_TIER_RPD
from app.core.auth import get_current_user
from app.database import UserORM

router = APIRouter()


@router.get("/quota", tags=["Quota"])
async def get_quota(current_user: UserORM = Depends(get_current_user)):
    """Live view of Gemini API free tier usage tracked in Supabase."""
    usage = await quota_manager.get_usage(settings.GEMINI_MODEL)
    return {
        "model": settings.GEMINI_MODEL,
        "free_tier_limits": {
            "requests_per_minute": FREE_TIER_RPM,
            "requests_per_day": FREE_TIER_RPD,
        },
        "current_usage": usage,
        "note": "Limits enforced at 93% of actual Google free tier to maintain safety buffer",
    }
