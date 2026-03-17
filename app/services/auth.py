"""Authentication service: password hashing, JWT, user management."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy import select, update

from app.config import settings
from app.models.database import PasswordResetToken, Session, User
from app.services.database import async_session

logger = logging.getLogger(__name__)


# --- Password hashing ---

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# --- JWT ---

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# --- User operations ---

async def get_user_by_email(email: str) -> User | None:
    async with async_session() as db:
        result = await db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: str) -> User | None:
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


async def create_user(*, email: str, password: str, name: str) -> User:
    async with async_session() as db:
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            name=name,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def link_session_to_user(*, session_id: str, user_id: str) -> None:
    async with async_session() as db:
        await db.execute(
            update(Session).where(Session.id == session_id).values(user_id=user_id)
        )
        await db.commit()


async def update_password(*, user_id: str, new_password: str) -> None:
    async with async_session() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(password_hash=hash_password(new_password))
        )
        await db.commit()


# --- Password reset tokens ---

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_reset_token(user_id: str) -> str:
    """Create a password reset token. Invalidates any existing unused tokens for this user."""
    raw_token = secrets.token_urlsafe(32)
    async with async_session() as db:
        # Invalidate all previous unused tokens for this user
        await db.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used == False)  # noqa: E712
            .values(used=True)
        )
        db.add(PasswordResetToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await db.commit()
    return raw_token


async def verify_reset_token(raw_token: str) -> str | None:
    """Verify a reset token. Returns user_id if valid, None otherwise."""
    token_hash = _hash_token(raw_token)
    async with async_session() as db:
        result = await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used == False,  # noqa: E712
                PasswordResetToken.expires_at > datetime.now(UTC),
            )
        )
        reset_token = result.scalar_one_or_none()
        if not reset_token:
            return None

        reset_token.used = True
        await db.commit()
        return reset_token.user_id
