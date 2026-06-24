import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.api.routes.matches import build_job_status, status_only, load_results
from app.core.db import get_db
from app.core.rate_limit import limiter
from app.core.config import settings
from app.core.security import (
    hash_password, verify_password, create_access_token,
    create_refresh_token, create_reset_token, decode_token,
)
from app.models import User, CreditEventType, MatchJob, JobStatus
from app.schemas.auth import (
    SignupRequest, LoginRequest, ForgotPasswordRequest,
    ResetPasswordRequest, RefreshRequest, UpdateProfileRequest, DeleteAccountRequest,
    TokenResponse, MeResponse, SearchSummary,
)
from app.schemas.match import JobStatusResponse, SearchDetailResponse
from app.services import account, credits

logger = logging.getLogger("profmatch.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
@limiter.limit("5/minute")
async def signup(request: Request, body: SignupRequest, db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(name=body.name, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    await credits.grant(
        db, user.id, settings.registered_start_credits, CreditEventType.GRANT_SIGNUP
    )
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(user.id, user.is_admin),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if user.is_disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    return TokenResponse(
        access_token=create_access_token(user.id, user.is_admin),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/forgot-password", status_code=200)
@limiter.limit("5/minute")
async def forgot_password(request: Request, body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user:
        token = create_reset_token(user.id)
        # No email provider yet — log the reset link so it can be picked up in dev.
        logger.info("Password reset requested for %s — token: %s", user.email, token)
    # Always 200 — never reveal whether the email exists.
    return {"message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password", status_code=200)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(body.token)
        if payload.get("type") != "reset":
            raise ValueError
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired token")
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid token")
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"message": "Password updated."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise ValueError
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or user.is_disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return TokenResponse(
        access_token=create_access_token(user.id, user.is_admin),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=MeResponse)
async def me(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    balance = await credits.get_balance(db, user.id)
    await db.commit()  # persist any lazy-regen surfaced during the balance read
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "credit_balance": balance,
    }


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.name = body.name
    balance = await credits.get_balance(db, user.id)
    await db.commit()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "credit_balance": balance,
    }


@router.get("/me/searches", response_model=list[SearchSummary])
async def my_searches(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    jobs = (await db.execute(
        select(MatchJob).where(MatchJob.user_id == user.id).order_by(MatchJob.created_at.desc())
    )).scalars().all()
    return [
        {
            "job_id": j.id,
            "status": j.status.value,
            "progress": j.progress,
            "university_url": j.university_url,
            "research_interests": j.research_interests,
            "total_professors_analyzed": j.total_analyzed,
            "match_count": len(j.results) if j.results else 0,
            "created_at": j.created_at,
        }
        for j in jobs
    ]


@router.get("/me/searches/{search_id}", response_model=SearchDetailResponse)
async def my_search(
    search_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = (await db.execute(
        status_only(select(MatchJob).where(MatchJob.id == search_id))
    )).scalar_one_or_none()
    if job is None or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search not found")
    results = await load_results(db, job.id) if job.status == JobStatus.DONE else None
    return SearchDetailResponse(
        **build_job_status(job, results).model_dump(),
        university_url=job.university_url,
        research_interests=job.research_interests,
        created_at=job.created_at,
    )


@router.delete("/me/searches/{search_id}", status_code=204)
async def delete_search(
    search_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = (await db.execute(
        status_only(select(MatchJob).where(MatchJob.id == search_id))
    )).scalar_one_or_none()
    if job is None or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search not found")
    await db.delete(job)
    await db.commit()


@router.delete("/me/searches", status_code=204)
async def clear_searches(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await account.clear_history(db, user.id)


@router.delete("/me", status_code=204)
@limiter.limit("3/minute")
async def delete_account(
    request: Request,
    body: DeleteAccountRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")
    await account.delete_account(db, user.id)
