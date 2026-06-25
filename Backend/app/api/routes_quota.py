from fastapi import APIRouter, Depends
from app.config import settings
from app.core.quota_manager import quota_manager
from app.core.auth import get_current_user
from app.database import UserORM

router = APIRouter()


@router.get("/quota", tags=["Quota"])
async def get_quota(current_user: UserORM = Depends(get_current_user)):
    """Live view of Gemini API quota usage tracked in Supabase."""
    usage = await quota_manager.get_usage(settings.GEMINI_MODEL)
    return {
        "model": settings.GEMINI_MODEL,
        "limits": {
            "requests_per_minute": settings.GEMINI_RPM_CAP,
            "requests_per_day": settings.GEMINI_RPD_CAP if settings.GEMINI_DAILY_CAP_ENABLED else None,
        },
        "current_usage": usage,
        "note": "RPM capped at 90% of Tier 1 limit; daily cap disabled (unlimited RPD on Tier 1)",
    }
