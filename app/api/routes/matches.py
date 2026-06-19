"""Match endpoints: POST creates a credit-spending job; GET polls status/results (anon gets one free search via cookie)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from app.api.deps import get_optional_user, get_or_set_anon_id, ANON_COOKIE
from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import limiter
from app.models import MatchJob, JobStatus, User
from app.schemas.match import JobStatusResponse, MatchResultsResponse
from app.services import credits
from app.services.cv import extract_cv_text
from app.workers.queue import enqueue_match_job

router = APIRouter(prefix="/matches", tags=["matches"])

STATUS_COLUMNS = (
    MatchJob.id, MatchJob.user_id, MatchJob.anon_session_id,
    MatchJob.status, MatchJob.progress, MatchJob.total_analyzed,
    MatchJob.processing_seconds, MatchJob.error,
    MatchJob.university_url, MatchJob.research_interests, MatchJob.created_at,
)


def status_only(stmt):
    """Apply load_only(STATUS_COLUMNS) so the heavy blobs aren't fetched."""
    return stmt.options(load_only(*STATUS_COLUMNS))


async def load_results(db: AsyncSession, job_id: str) -> list | None:
    """Fetch just the results blob for a finished job (deferred columns can't async lazy-load)."""
    return (await db.execute(
        select(MatchJob.results).where(MatchJob.id == job_id)
    )).scalar_one_or_none()


async def _anon_search_count(db: AsyncSession, anon_id: str) -> int:
    stmt = select(func.count(MatchJob.id)).where(MatchJob.anon_session_id == anon_id)
    return int((await db.execute(stmt)).scalar_one())


def build_job_status(job: MatchJob, results: list | None = None) -> JobStatusResponse:
    """Job row -> status envelope; results is passed in (loaded only when done), not read off the row."""
    result = None
    if job.status == JobStatus.DONE and results is not None:
        result = MatchResultsResponse(
            session_id=job.id,
            matches=results,
            total_professors_analyzed=job.total_analyzed or len(results),
            processing_time_seconds=job.processing_seconds or 0.0,
        )
    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        progress=job.progress,
        result=result,
        error=job.error,
    )


@router.post("", response_model=JobStatusResponse, status_code=202)
@limiter.limit("10/minute")
async def create_match(
    request: Request,
    response: Response,
    university_url: str = Form(..., examples=["https://www.stanford.edu"]),
    research_interests: str = Form(..., examples=["graph neural networks, representation learning"]),
    cv: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    cv_bytes = await cv.read()
    cv_text = extract_cv_text(cv_bytes, cv.filename or "cv")
    if not cv_text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Could not read CV")

    job = MatchJob(
        university_url=university_url,
        research_interests=research_interests,
        cv_text=cv_text,
        status=JobStatus.QUEUED,
    )

    if user:
        job.user_id = user.id
        db.add(job)
        await db.flush()  # need job.id as the spend reference
        ok = await credits.try_spend(db, user.id, reference=job.id)
        if not ok:
            await db.rollback()
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Out of credits")
    else:
        anon_id = get_or_set_anon_id(request)
        used = await _anon_search_count(db, anon_id)
        if used >= settings.anon_free_searches:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                "Free search used. Please sign up to continue.",
            )
        job.anon_session_id = anon_id
        db.add(job)
        await db.flush()
        # Cross-site cookies need SameSite=None+Secure in prod; Lax on localhost dev.
        secure = settings.env != "development"
        response.set_cookie(
            ANON_COOKIE,
            anon_id,
            httponly=True,
            secure=secure,
            samesite="none" if secure else "lax",
            max_age=60 * 60 * 24 * 365,
        )

    await db.commit()

    try:
        await enqueue_match_job(job.id)
    except Exception:
        # Queue down — refund and drop the job so the user isn't charged for nothing.
        if job.user_id:
            await credits.refund(db, job.user_id, reference=job.id)
        await db.delete(job)
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Job queue is unavailable, please try again shortly.",
        )

    return JobStatusResponse(job_id=job.id, status=job.status.value, progress=job.progress)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_match(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    job = (await db.execute(
        status_only(select(MatchJob).where(MatchJob.id == job_id))
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    # ownership check — user_id match, or anon cookie match
    owns = (user and job.user_id == user.id) or (
        job.anon_session_id and job.anon_session_id == request.cookies.get(ANON_COOKIE)
    )
    if not owns:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your job")

    # Only pull the (large) results blob once the job is actually done.
    results = await load_results(db, job.id) if job.status == JobStatus.DONE else None
    return build_job_status(job, results)
