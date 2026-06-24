from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from passlib.context import CryptContext
from app.core.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


def _create_token(sub: str, expires: timedelta, token_type: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {"sub": sub, "type": token_type, "iat": now, "exp": now + expires}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(sub: str, is_admin: bool = False) -> str:
    return _create_token(
        sub,
        timedelta(minutes=settings.access_token_expire_minutes),
        "access",
        {"is_admin": is_admin},
    )


def create_refresh_token(sub: str) -> str:
    return _create_token(sub, timedelta(days=settings.refresh_token_expire_days), "refresh")


def create_reset_token(sub: str) -> str:
    return _create_token(sub, timedelta(minutes=30), "reset")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
