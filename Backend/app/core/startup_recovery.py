import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.database import AsyncSessionLocal, JobORM, JobPageORM

logger = logging.getLogger(__name__)

# A page existing at all means _init_job_pages ran for its job, i.e. work began —
# so every stale non-terminal page (whether the job's own status column reads
# "queued" or "processing") gets the same message. Note: JobORM.status only ever
# reads "processing" via the /retry path (routes_jobs.py) — a normal upload-created
# job stays "queued" in the DB for its whole lifecycle until a terminal status is
# set at the end of run_batch_processor. So "queued" vs "processing" on the job
# row does NOT reliably distinguish "never started" from "died mid-flight" — only
# the presence (or absence) of job_pages rows, and page-level staleness, does that.
_INTERRUPTED_MESSAGE = "Interrupted by server restart"
_NEVER_STARTED_MESSAGE = "Interrupted before processing started"

_NON_TERMINAL_PAGE_STATUSES = ("queued", "processing")
_TERMINAL_JOB_STATUSES = ("done", "completed_with_errors", "error")


async def recover_interrupted_jobs() -> None:
    """
    One-shot startup sweep: flip pages/jobs orphaned by a server crash so the
    existing /retry endpoint can rescue them. Runs once, before serving traffic.
    """
    stale_cutoff = datetime.utcnow() - timedelta(minutes=settings.STARTUP_RECOVERY_STALE_MINUTES)
    now = datetime.utcnow()

    async with AsyncSessionLocal() as session:
        non_terminal_jobs_subq = select(JobORM.id).where(
            JobORM.status.notin_(_TERMINAL_JOB_STATUSES)
        )
        stale_page_filter = (
            JobPageORM.status.in_(_NON_TERMINAL_PAGE_STATUSES),
            JobPageORM.updated_at < stale_cutoff,
            JobPageORM.job_id.in_(non_terminal_jobs_subq),
        )

        stale_job_ids = set(
            (
                await session.execute(
                    select(JobPageORM.job_id).where(*stale_page_filter).distinct()
                )
            ).scalars().all()
        )

        flip_result = await session.execute(
            update(JobPageORM)
            .where(*stale_page_filter)
            .values(status="failed", error_message=_INTERRUPTED_MESSAGE, updated_at=now)
        )
        pages_flipped = flip_result.rowcount or 0

        # Zero-page orphans: job row created at upload time, but the process died
        # before run_batch_processor ever reached _init_job_pages — nothing to flip,
        # nothing for /retry to rescue either, so go straight to a terminal state.
        any_pages_subq = select(JobPageORM.job_id).distinct()
        zero_page_job_ids = list(
            (
                await session.execute(
                    select(JobORM.id).where(
                        JobORM.status == "queued",
                        JobORM.created_at < stale_cutoff,
                        JobORM.id.notin_(any_pages_subq),
                    )
                )
            ).scalars().all()
        )
        if zero_page_job_ids:
            await session.execute(
                update(JobORM)
                .where(JobORM.id.in_(zero_page_job_ids))
                .values(status="error", completed_at=now)
            )
            logger.warning(
                "Startup recovery: %d job(s) had zero job_pages rows — %s",
                len(zero_page_job_ids), _NEVER_STARTED_MESSAGE,
            )

        # Reconciliation candidates: every non-terminal job that HAS pages — not just
        # the ones we just flipped. A job whose pages were ALL already "done" before
        # this sweep ran (crashed between extraction and Excel build) never needed a
        # flip, but still needs reconciling (see the excel_path-null branch below).
        candidate_job_ids = set(
            (
                await session.execute(
                    select(JobPageORM.job_id)
                    .where(JobPageORM.job_id.in_(non_terminal_jobs_subq))
                    .distinct()
                )
            ).scalars().all()
        )

        jobs_reconciled = 0
        jobs_skipped = 0

        for job_id in candidate_job_ids:
            pages = (
                await session.execute(
                    select(JobPageORM.status, JobPageORM.updated_at).where(
                        JobPageORM.job_id == job_id
                    )
                )
            ).all()

            active = sum(1 for status, _ in pages if status in _NON_TERMINAL_PAGE_STATUSES)
            if active > 0:
                # Still has fresh non-terminal pages — a live instance may genuinely be
                # working on this job. Leave it alone; normal completion finalizes it.
                jobs_skipped += 1
                continue

            if job_id not in stale_job_ids:
                # Nothing here needed flipping (all pages were already terminal before
                # this sweep). Only reconcile if that terminal state itself is stale —
                # otherwise this could be a job that JUST finished extraction and is
                # about to move into the save/Excel-build step on a live instance.
                max_updated_at = max(updated_at for _, updated_at in pages)
                if max_updated_at >= stale_cutoff:
                    jobs_skipped += 1
                    continue

            done = sum(1 for status, _ in pages if status == "done")
            failed = sum(1 for status, _ in pages if status == "failed")

            job = await session.get(JobORM, job_id)
            if job is None:
                continue

            if done == 0:
                new_status = "error"
            elif failed > 0:
                new_status = "completed_with_errors"
            elif job.excel_path is None:
                new_status = "error"
            else:
                new_status = "done"

            await session.execute(
                update(JobORM)
                .where(JobORM.id == job_id)
                .values(
                    status=new_status,
                    completed_pages=done,
                    failed_pages=failed,
                    completed_at=now,
                )
            )
            jobs_reconciled += 1

        await session.commit()

    logger.info(
        "Startup recovery: %d page(s) flipped, %d job(s) reconciled, %d job(s) skipped (still active)",
        pages_flipped, jobs_reconciled + len(zero_page_job_ids), jobs_skipped,
    )
