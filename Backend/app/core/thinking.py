"""Blueprint thinking toggle → Gemini thinking_budget, per model.

Pure helpers (no SDK imports) so the mapping is unit-testable in isolation.
"""
import logging
from typing import Optional

from app.config import GEMINI_THINKING_LIMITS

logger = logging.getLogger(__name__)

DYNAMIC = -1


def thinking_limits(model: str) -> Optional[dict]:
    """Limits for the longest matching model prefix; None = no thinking support."""
    matches = [k for k in GEMINI_THINKING_LIMITS if model.startswith(k)]
    if not matches:
        return None
    return GEMINI_THINKING_LIMITS[max(matches, key=len)]


def resolve_thinking_budget(model: str, enabled: bool) -> Optional[int]:
    """Budget to send for a blueprint's thinking toggle, or None to send no
    thinking_config at all (model has no thinking support)."""
    limits = thinking_limits(model)
    if limits is None:
        if not any(model.startswith(k) for k in GEMINI_THINKING_LIMITS):
            logger.warning(f"Model {model!r} not in GEMINI_THINKING_LIMITS — sending no thinking_config")
        return None
    return DYNAMIC if enabled else limits["off"]


def default_thinking_budget(model: str) -> Optional[int]:
    """What the model does when no thinking_config is sent."""
    limits = thinking_limits(model)
    return limits["default"] if limits else None


def thinking_label(model: Optional[str], budget: Optional[int]) -> str:
    if budget is None:
        return "N/A"
    if budget == DYNAMIC:
        return "On (dynamic)"
    if budget == 0:
        return "Off"
    limits = thinking_limits(model) if model else None
    if limits and budget == limits["off"]:
        return f"Min ({budget})"
    return f"{budget} tokens"
