"""AI observability read API — per-user aggregates over llm_calls."""
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select

from app.config import settings
from app.core.auth import get_current_user
from app.core.thinking import thinking_label
from app.database import AsyncSessionLocal, BlueprintORM, JobORM, LLMCallORM, ProjectORM, UserORM, db_retry

router = APIRouter(prefix="/observability", tags=["Observability"])

_LATENCY_BUCKETS = [(0, 2000, "<2s"), (2000, 5000, "2–5s"), (5000, 10000, "5–10s"),
                    (10000, 20000, "10–20s"), (20000, 40000, "20–40s"), (40000, None, "40s+")]


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return int(round(s[lo] + (s[hi] - s[lo]) * (k - lo)))


def _empty_bucket() -> dict:
    return {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
            "thinking_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def _add(bucket: dict, r) -> None:
    bucket["calls"] += 1
    bucket["errors"] += 1 if r.status != "success" else 0
    bucket["input_tokens"] += r.input_tokens or 0
    bucket["output_tokens"] += r.output_tokens or 0
    bucket["thinking_tokens"] += r.thinking_tokens or 0
    bucket["total_tokens"] += r.total_tokens or 0
    bucket["cost_usd"] += r.cost_usd or 0.0


@router.get("/overview")
@db_retry()
async def overview(days: int = Query(30, ge=1, le=365), current_user: UserORM = Depends(get_current_user)):
    """Totals, daily series and breakdowns for the caller's Gemini usage."""
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)
    since = datetime.combine(start_date, datetime.min.time())

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                LLMCallORM.created_at, LLMCallORM.model, LLMCallORM.operation, LLMCallORM.status,
                LLMCallORM.error_type, LLMCallORM.project_id, LLMCallORM.job_id, LLMCallORM.page_label,
                LLMCallORM.input_tokens, LLMCallORM.output_tokens, LLMCallORM.thinking_tokens,
                LLMCallORM.cached_tokens, LLMCallORM.total_tokens, LLMCallORM.latency_ms, LLMCallORM.cost_usd,
            ).where(LLMCallORM.user_id == current_user.id, LLMCallORM.created_at >= since)
        )).all()

        project_ids = {r.project_id for r in rows if r.project_id}
        project_names = {}
        if project_ids:
            project_names = {
                p.id: p.name for p in (await session.execute(
                    select(ProjectORM.id, ProjectORM.name).where(ProjectORM.id.in_(project_ids))
                )).all()
            }

    totals = _empty_bucket()
    totals["cached_tokens"] = 0
    daily = {start_date + timedelta(days=i): {**_empty_bucket(), "latencies": []} for i in range(days)}
    by_model: dict = defaultdict(_empty_bucket)
    by_operation: dict = defaultdict(_empty_bucket)
    by_project: dict = defaultdict(_empty_bucket)
    errors_by_type: dict = defaultdict(int)
    latencies: list[int] = []
    pages: set = set()

    for r in rows:
        _add(totals, r)
        totals["cached_tokens"] += r.cached_tokens or 0
        day = daily.get(r.created_at.date())
        if day is not None:
            _add(day, r)
        _add(by_model[r.model], r)
        _add(by_operation[r.operation], r)
        if r.project_id:
            _add(by_project[r.project_id], r)
        if r.status == "success":
            latencies.append(r.latency_ms or 0)
            if day is not None:
                day["latencies"].append(r.latency_ms or 0)
            if r.operation == "extract" and r.job_id:
                pages.add((r.job_id, r.page_label))
        else:
            errors_by_type[r.error_type or "other"] += 1

    histogram = [
        {"label": label, "count": sum(1 for v in latencies if v >= lo and (hi is None or v < hi))}
        for lo, hi, label in _LATENCY_BUCKETS
    ]

    daily_out = []
    for d, b in daily.items():
        lat = b.pop("latencies")
        daily_out.append({**b, "date": d.isoformat(), "cost_usd": round(b["cost_usd"], 6),
                          "p50_latency_ms": _percentile(lat, 0.5), "p95_latency_ms": _percentile(lat, 0.95)})

    def _rows(d: dict, key: str, names: dict | None = None) -> list:
        out = [{key: (names.get(k, "Deleted project") if names is not None else k), **v,
                "cost_usd": round(v["cost_usd"], 6)} for k, v in d.items()]
        return sorted(out, key=lambda x: x["cost_usd"], reverse=True)

    calls = totals["calls"]
    return {
        "range_days": days,
        "current_model": settings.GEMINI_MODEL,
        "totals": {
            **totals,
            "cost_usd": round(totals["cost_usd"], 6),
            "success": calls - totals["errors"],
            "error_rate": round(totals["errors"] / calls, 4) if calls else 0.0,
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "p50_latency_ms": _percentile(latencies, 0.5),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "pages_extracted": len(pages),
            "cost_per_page_usd": round(totals["cost_usd"] / len(pages), 6) if pages else 0.0,
        },
        "daily": daily_out,
        "by_model": _rows(by_model, "model"),
        "by_operation": _rows(by_operation, "operation"),
        "by_project": _rows(by_project, "project", project_names)[:8],
        "errors_by_type": sorted(
            [{"error_type": k, "count": v} for k, v in errors_by_type.items()], key=lambda x: -x["count"]
        ),
        "latency_histogram": histogram,
    }


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, "Job not found")


# Per-job aggregates over llm_calls. Every expression is portable (Postgres + SQLite).
_JOB_AGG = (
    func.count().label("calls"),
    func.sum(case((LLMCallORM.status != "success", 1), else_=0)).label("errors"),
    func.sum(case((LLMCallORM.attempt > 1, 1), else_=0)).label("retries"),
    func.sum(LLMCallORM.input_tokens).label("input_tokens"),
    func.sum(LLMCallORM.output_tokens).label("output_tokens"),
    func.sum(LLMCallORM.thinking_tokens).label("thinking_tokens"),
    func.sum(LLMCallORM.cached_tokens).label("cached_tokens"),
    func.sum(LLMCallORM.total_tokens).label("total_tokens"),
    func.sum(LLMCallORM.cost_usd).label("cost_usd"),
    func.avg(case((LLMCallORM.status == "success", LLMCallORM.latency_ms), else_=None)).label("avg_latency_ms"),
    func.max(case((LLMCallORM.status == "success", LLMCallORM.latency_ms), else_=None)).label("max_latency_ms"),
    func.sum(LLMCallORM.latency_ms).label("ai_time_ms"),
    func.max(LLMCallORM.model).label("model"),
    func.count(func.distinct(LLMCallORM.model)).label("model_count"),
)

# Whitelisted sort keys → JobORM column or aggregate label.
_JOB_SORTS = {
    "created_at": None,
    "cost": "cost_usd",
    "tokens": "total_tokens",
    "latency": "avg_latency_ms",
    "calls": "calls",
    "errors": "errors",
}


def _job_thinking_label(job) -> str:
    # Jobs carry no model; a positive budget with thinking off is a model's minimum (e.g. 2.5 Pro).
    if job.thinking is False and job.thinking_budget and job.thinking_budget > 0:
        return f"Min ({job.thinking_budget})"
    return thinking_label(None, job.thinking_budget)


async def _job_rows(session, user_id, *, since=None, job_id=None, sort="created_at",
                    order="desc", limit=20, offset=0) -> tuple[int, list[dict]]:
    filters = [LLMCallORM.user_id == user_id, LLMCallORM.job_id.isnot(None)]
    if since is not None:
        filters.append(LLMCallORM.created_at >= since)
    if job_id is not None:
        filters.append(LLMCallORM.job_id == job_id)
    agg = (
        select(LLMCallORM.job_id.label("job_id"), *_JOB_AGG)
        .where(*filters).group_by(LLMCallORM.job_id).subquery()
    )
    base = (
        select(agg, JobORM, ProjectORM.name.label("project_name"), BlueprintORM.name.label("blueprint_name"))
        .join(JobORM, JobORM.id == agg.c.job_id)
        .outerjoin(ProjectORM, ProjectORM.id == JobORM.project_id)
        .outerjoin(BlueprintORM, BlueprintORM.id == JobORM.blueprint_id)
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    sort_key = _JOB_SORTS.get(sort)
    sort_col = agg.c[sort_key] if sort_key else JobORM.created_at
    ordering = sort_col.asc() if order == "asc" else sort_col.desc()
    rows = (await session.execute(
        base.order_by(ordering, JobORM.created_at.desc()).limit(limit).offset(offset)
    )).all()

    # p50/p95 need raw latencies — fetched only for the jobs on this page.
    latencies: dict = defaultdict(list)
    job_ids = [r.job_id for r in rows]
    if job_ids:
        for jid, ms in (await session.execute(
            select(LLMCallORM.job_id, LLMCallORM.latency_ms)
            .where(*filters, LLMCallORM.job_id.in_(job_ids), LLMCallORM.status == "success")
        )).all():
            latencies[jid].append(ms or 0)

    items = []
    for r in rows:
        job = r.JobORM
        done = job.completed_pages or 0
        cost = float(r.cost_usd or 0)
        tokens = int(r.total_tokens or 0)
        items.append({
            "job_id": str(job.id),
            "project_id": str(job.project_id) if job.project_id else None,
            "project_name": r.project_name,
            "blueprint_name": r.blueprint_name,
            "status": job.status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "duration_ms": (
                int((job.completed_at - job.created_at).total_seconds() * 1000)
                if job.completed_at and job.created_at else None
            ),
            "total_files": job.total_files or 0,
            "total_pages": job.total_pages or 0,
            "completed_pages": done,
            "failed_pages": job.failed_pages or 0,
            "thinking": job.thinking,
            "thinking_budget": job.thinking_budget,
            "thinking_label": _job_thinking_label(job),
            "model": r.model if r.model_count <= 1 else f"{r.model} +{r.model_count - 1}",
            "calls": r.calls or 0,
            "errors": int(r.errors or 0),
            "retries": int(r.retries or 0),
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
            "thinking_tokens": int(r.thinking_tokens or 0),
            "cached_tokens": int(r.cached_tokens or 0),
            "total_tokens": tokens,
            "cost_usd": round(cost, 6),
            "cost_per_page_usd": round(cost / done, 6) if done else None,
            "tokens_per_page": int(tokens / done) if done else None,
            "avg_latency_ms": int(r.avg_latency_ms or 0),
            "max_latency_ms": int(r.max_latency_ms or 0),
            "p50_latency_ms": _percentile(latencies[r.job_id], 0.5),
            "p95_latency_ms": _percentile(latencies[r.job_id], 0.95),
            "ai_time_ms": int(r.ai_time_ms or 0),
        })
    return total, items


@router.get("/jobs")
@db_retry()
async def list_jobs(
    days: int = Query(30, ge=1, le=365),
    sort: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
):
    """Per-job AI usage — one row per extraction job, for side-by-side comparison."""
    since = datetime.combine(datetime.utcnow().date() - timedelta(days=days - 1), datetime.min.time())
    async with AsyncSessionLocal() as session:
        total, items = await _job_rows(
            session, current_user.id, since=since, sort=sort, order=order, limit=limit, offset=offset,
        )
    return {"total": total, "items": items}


@router.get("/jobs/{job_id}")
@db_retry()
async def job_detail(job_id: str, current_user: UserORM = Depends(get_current_user)):
    """One job's AI usage summary (all time)."""
    async with AsyncSessionLocal() as session:
        _, items = await _job_rows(session, current_user.id, job_id=_parse_uuid(job_id), limit=1)
    if not items:
        raise HTTPException(404, "Job not found")
    return items[0]


@router.get("/calls")
@db_retry()
async def list_calls(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    job_id: Optional[str] = Query(None),
    current_user: UserORM = Depends(get_current_user),
):
    """Individual Gemini calls for the caller, newest first — optionally one job's."""
    filters = [LLMCallORM.user_id == current_user.id]
    if job_id:
        filters.append(LLMCallORM.job_id == _parse_uuid(job_id))
    async with AsyncSessionLocal() as session:
        total = (await session.execute(
            select(func.count()).select_from(LLMCallORM).where(*filters)
        )).scalar() or 0
        rows = (await session.execute(
            select(LLMCallORM, ProjectORM.name)
            .outerjoin(ProjectORM, ProjectORM.id == LLMCallORM.project_id)
            .where(*filters)
            .order_by(LLMCallORM.created_at.desc())
            .limit(limit).offset(offset)
        )).all()

    return {
        "total": total,
        "items": [
            {
                "id": str(c.id),
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "operation": c.operation,
                "model": c.model,
                "project_name": project_name,
                "job_id": str(c.job_id) if c.job_id else None,
                "page_label": c.page_label,
                "input_mode": c.input_mode,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "thinking_tokens": c.thinking_tokens,
                "cached_tokens": c.cached_tokens,
                "total_tokens": c.total_tokens,
                "latency_ms": c.latency_ms,
                "cost_usd": c.cost_usd,
                "status": c.status,
                "error_type": c.error_type,
                "error_message": c.error_message,
                "attempt": c.attempt,
                "thinking_budget": c.thinking_budget,
                "thinking_label": thinking_label(c.model, c.thinking_budget),
            }
            for c, project_name in rows
        ],
    }
