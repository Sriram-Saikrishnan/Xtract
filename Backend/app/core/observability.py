"""
AI observability — logs every Gemini call (model, tokens, latency, cost,
outcome) to the llm_calls table.

Call-site context (who/what triggered the call) flows in through a
ContextVar, so gemini_client functions don't need extra parameters: the
batch processor and routes call set_llm_context() before invoking Gemini.
Logging is fire-and-forget and never raises — a failed log write must
never fail an extraction.
"""
import asyncio
import logging
import uuid
from contextvars import ContextVar
from typing import Any, Optional

from app.config import settings
from app.database import AsyncSessionLocal, LLMCallORM

logger = logging.getLogger(__name__)

_llm_context: ContextVar[dict] = ContextVar("llm_context", default={})

# Strong refs so pending log writes aren't garbage-collected mid-flight.
_pending: set[asyncio.Task] = set()
# Log writes share the small PgBouncer pool with extraction — keep them bounded.
_write_sem: Optional[asyncio.Semaphore] = None

_UUID_KEYS = ("user_id", "project_id", "blueprint_id", "job_id")


def set_llm_context(**kwargs: Any) -> None:
    """Merge kwargs into the current task's LLM context. Copy-on-write, so a
    child task (e.g. one page in asyncio.gather) never leaks into siblings."""
    merged = {**_llm_context.get(), **{k: v for k, v in kwargs.items() if v is not None}}
    _llm_context.set(merged)


def get_llm_context() -> dict:
    return _llm_context.get()


def _pricing_for(model: str) -> Optional[dict]:
    pricing = settings.GEMINI_PRICING
    if model in pricing:
        return pricing[model]
    # Versioned names like "gemini-2.5-flash-preview-05-20" → longest matching prefix.
    matches = [k for k in pricing if model.startswith(k)]
    return pricing[max(matches, key=len)] if matches else None


def compute_cost(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int, cached_tokens: int) -> float:
    """Estimated USD cost. Cached tokens are a subset of input tokens and are
    billed at the cached rate; thinking tokens are billed at the output rate."""
    rates = _pricing_for(model)
    if not rates:
        return 0.0
    uncached = max(input_tokens - cached_tokens, 0)
    cost = (
        uncached * rates.get("input", 0.0)
        + cached_tokens * rates.get("cached", rates.get("input", 0.0))
        + (output_tokens + thinking_tokens) * rates.get("output", 0.0)
    ) / 1_000_000
    return round(cost, 8)


def usage_from_response(response) -> dict:
    meta = getattr(response, "usage_metadata", None)

    def _get(name: str) -> int:
        return int(getattr(meta, name, None) or 0) if meta is not None else 0

    return {
        "input_tokens": _get("prompt_token_count"),
        "output_tokens": _get("candidates_token_count"),
        "thinking_tokens": _get("thoughts_token_count"),
        "cached_tokens": _get("cached_content_token_count"),
        "total_tokens": _get("total_token_count"),
    }


def classify_error(err: BaseException) -> str:
    import httpx

    s = str(err)
    low = s.lower()
    if isinstance(err, httpx.TimeoutException) or "timeout" in low or "timed out" in low:
        return "timeout"
    if "429" in s or "resource_exhausted" in low or "quota" in low:
        return "rate_limit"
    if "503" in s or "service_unavailable" in low:
        return "unavailable"
    if "404" in s or "not_found" in low or "not found" in low or "is not supported" in low:
        return "model_not_found"
    return "other"


def _to_uuid(value) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _write(row: dict) -> None:
    global _write_sem
    if _write_sem is None:
        _write_sem = asyncio.Semaphore(2)
    try:
        async with _write_sem, AsyncSessionLocal() as session:
            session.add(LLMCallORM(**row))
            await session.commit()
    except Exception as e:
        logger.warning(f"llm_calls log write failed (ignored): {e}")


def record_llm_call(
    *,
    model: str,
    latency_ms: int,
    status: str,
    response=None,
    error: Optional[BaseException] = None,
    attempt: int = 1,
    input_mode: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> None:
    """Schedule a non-blocking llm_calls insert using the current context."""
    try:
        ctx = get_llm_context()
        usage = usage_from_response(response) if response is not None else {
            "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0, "total_tokens": 0,
        }
        if not usage["total_tokens"]:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"] + usage["thinking_tokens"]
        row = {
            **{k: _to_uuid(ctx.get(k)) for k in _UUID_KEYS},
            "page_label": (ctx.get("page_label") or None),
            "operation": ctx.get("operation", "unknown"),
            "model": model,
            "input_mode": input_mode,
            **usage,
            "latency_ms": int(latency_ms),
            "cost_usd": compute_cost(
                model, usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"], usage["cached_tokens"]
            ),
            "status": status,
            "error_type": classify_error(error) if error is not None else None,
            "error_message": str(error)[:1000] if error is not None else None,
            "attempt": attempt,
            "thinking_budget": thinking_budget,
        }
        task = asyncio.get_running_loop().create_task(_write(row))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as e:
        logger.warning(f"record_llm_call failed (ignored): {e}")
