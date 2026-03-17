"""Authentication routes: signup, login, forgot/reset password."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.dependencies import CurrentUser
from app.models.database import SearchHistory
from app.models.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MatchResult,
    MessageResponse,
    ResetPasswordRequest,
    SearchHistoryDetail,
    SearchHistorySummary,
    SignupRequest,
    UserResponse,
)
from app.services.auth import (
    create_access_token,
    create_reset_token,
    create_user,
    get_user_by_email,
    link_session_to_user,
    update_password,
    verify_password,
    verify_reset_token,
)
from app.services.database import async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        created_at=user.created_at,
    )


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(body: SignupRequest):
    """Create a new user account."""
    if await get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await create_user(email=body.email, password=body.password, name=body.name)

    if body.session_id:
        await link_session_to_user(session_id=body.session_id, user_id=user.id)

    return AuthResponse(
        user=_user_response(user),
        access_token=create_access_token(user.id),
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    """Log in with email and password."""
    user = await get_user_by_email(body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if body.session_id:
        await link_session_to_user(session_id=body.session_id, user_id=user.id)

    return AuthResponse(
        user=_user_response(user),
        access_token=create_access_token(user.id),
    )


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest):
    """Request a password reset. Always returns success to avoid leaking user existence."""
    user = await get_user_by_email(body.email)
    if user:
        raw_token = await create_reset_token(user.id)
        reset_link = f"{settings.frontend_url}/reset-password?token={raw_token}"
        # TODO: Send email with reset_link. For now, log it in development.
        logger.info("Password reset link for %s: %s", body.email, reset_link)

    return MessageResponse(message="If an account exists with that email, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest):
    """Reset password using a valid reset token."""
    user_id = await verify_reset_token(body.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    await update_password(user_id=user_id, new_password=body.new_password)
    return MessageResponse(message="Password reset successful.")


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser):
    """Get current authenticated user profile."""
    return _user_response(user)


@router.get("/me/searches", response_model=list[SearchHistorySummary])
async def list_searches(user: CurrentUser):
    """List all saved searches for the current user."""
    async with async_session() as db:
        result = await db.execute(
            select(SearchHistory)
            .where(SearchHistory.user_id == user.id)
            .order_by(SearchHistory.created_at.desc())
        )
        rows = result.scalars().all()

    return [
        SearchHistorySummary(
            id=row.id,
            match_id=row.match_id,
            university=row.university,
            research_interests=row.research_interests,
            result_count=len(row.results),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/me/searches/{search_id}", response_model=SearchHistoryDetail)
async def get_search(search_id: str, user: CurrentUser):
    """Get full details of a saved search."""
    async with async_session() as db:
        result = await db.execute(
            select(SearchHistory).where(
                SearchHistory.id == search_id,
                SearchHistory.user_id == user.id,
            )
        )
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Search not found")

    return SearchHistoryDetail(
        id=row.id,
        match_id=row.match_id,
        university=row.university,
        research_interests=row.research_interests,
        results=[MatchResult(**r) for r in row.results],
        total_time=row.total_time,
        created_at=row.created_at,
    )
