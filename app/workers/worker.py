"""Background worker: runs the 5-stage pipeline, checkpointing each stage to the
job row so a client refresh or worker crash never restarts from zero.

Run with:  arq app.workers.worker.WorkerSettings
"""
from sqlalchemy import select
import time
from arq.connections import RedisSettings
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import MatchJob, JobStatus
from app.services import credits
from app.services.pipeline import profile, discovery, enrichment, scoring, ranking


async def _set(job: MatchJob, db, *, status=None, progress=None, **fields):
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    for k, v in fields.items():
        setattr(job, k, v)
    await db.commit()


async def run_match_job(ctx, job_id: str):
    async with SessionLocal() as db:
        job = (await db.execute(select(MatchJob).where(MatchJob.id == job_id))).scalar_one_or_none()
        if job is None:
            return
        try:
            started = time.monotonic()
            # Stage 1 — resume if already done
            if job.student_profile is None:
                await _set(job, db, status=JobStatus.PARSING, progress=10)
                student_profile = await profile.run(job.cv_text, job.research_interests)
                await _set(job, db, student_profile=student_profile, progress=20)
            student_profile = job.student_profile

            # Stage 2
            if job.faculty is None:
                await _set(job, db, status=JobStatus.DISCOVERING, progress=30)
                field = student_profile.get("profile_text", job.research_interests)
                faculty = await discovery.run(job.university_url, field)
                await _set(job, db, faculty=faculty, progress=45)
            faculty = job.faculty

            # Stage 3
            if job.enriched is None:
                await _set(job, db, status=JobStatus.ENRICHING, progress=55)
                enriched = await enrichment.run(faculty)
                await _set(job, db, enriched=enriched, progress=70)
            enriched = job.enriched

            # Stage 4 + 5
            await _set(job, db, status=JobStatus.SCORING, progress=80)
            scored = await scoring.run(student_profile["profile_text"], enriched)
            await _set(job, db, status=JobStatus.RANKING, progress=90)
            results = await ranking.run(student_profile["profile_text"], scored)

            elapsed = round(time.monotonic() - started, 2)
            await _set(
                job, db,
                status=JobStatus.DONE, progress=100, results=results,
                total_analyzed=len(faculty or []),
                processing_seconds=elapsed,
            )
        except Exception as exc:  # noqa: BLE001
            await _set(job, db, status=JobStatus.FAILED, error=str(exc))
            # refund the credit on hard failure (registered users only)
            if job.user_id:
                await credits.refund(db, job.user_id, reference=job.id)
                await db.commit()


class WorkerSettings:
    functions = [run_match_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.arq_queue_name
    max_jobs = 10
    job_timeout = 300
