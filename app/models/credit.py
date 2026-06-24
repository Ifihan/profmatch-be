import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class CreditEventType(str, enum.Enum):
    GRANT_SIGNUP = "grant_signup"
    GRANT_REGEN = "grant_regen"
    GRANT_PROMO = "grant_promo"
    GRANT_PURCHASE = "grant_purchase"
    GRANT_REFUND = "grant_refund"
    GRANT_ADMIN = "grant_admin"
    SPEND_SEARCH = "spend_search"


class CreditEvent(Base):
    """Append-only ledger. Balance is the SUM(delta) for a user."""
    __tablename__ = "credit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    event_type: Mapped[CreditEventType] = mapped_column(Enum(CreditEventType), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # +grant / -spend
    reference: Mapped[str | None] = mapped_column(String, nullable=True)  # job_id, payment ref...
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_credit_user_created", "user_id", "created_at"),)
