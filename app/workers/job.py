"""The 5-stage match pipeline, backend-agnostic — called by both the ARQ worker
and the Cloud Tasks HTTP handler. Checkpoints each stage to the job row."""
import time

from sqlalchemy import select
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


async def process_match_job(job_id: str) -> None:
    async with SessionLocal() as db:
        job = (await db.execute(select(MatchJob).where(MatchJob.id == job_id))).scalar_one_or_none()
        if job is None or job.status == JobStatus.DONE:
            return  # idempotent: a Cloud Tasks retry of a finished job is a no-op
        try:
            started = time.monotonic()
            # Stage 1 — resume if already done
            if job.student_profile is None:
                await _set(job, db, status=JobStatus.PARSING, progress=10)
                student_profile = await profile.run(job.cv_text, job.research_interests)
                await _set(job, db, student_profile=student_profile, progress=20)
            student_profile = job.student_profile

            # Stage 2 — query by the stated interests (not the diluted profile text)
            if job.faculty is None:
                await _set(job, db, status=JobStatus.DISCOVERING, progress=30)
                faculty = await discovery.run(
                    job.university_url, job.research_interests, student_profile.get("key_topics"),
                )
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
            results = await ranking.run(
                student_profile["profile_text"], scored, interests=job.research_interests,
            )

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
