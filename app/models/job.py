import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum, JSON, Integer, Text, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    DISCOVERING = "discovering"
    ENRICHING = "enriching"
    SCORING = "scoring"
    RANKING = "ranking"
    DONE = "done"
    FAILED = "failed"


class MatchJob(Base):
    __tablename__ = "match_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # owner is either a user_id or an anonymous session id
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    anon_session_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100

    # inputs
    university_url: Mapped[str] = mapped_column(String, nullable=False)
    research_interests: Mapped[str] = mapped_column(Text, nullable=False)
    cv_text: Mapped[str] = mapped_column(Text, nullable=False)

    # checkpointed stage outputs (resume from last completed stage)
    student_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    faculty: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enriched: Mapped[list | None] = mapped_column(JSON, nullable=True)
    results: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # response-wrapper metrics
    total_analyzed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
