from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class Publication(BaseModel):
    """Academic publication."""
    title: str
    authors: list[str]
    year: int
    venue: str | None = None
    abstract: str | None = None
    citation_count: int = 0
    url: str | None = None


class CitationMetrics(BaseModel):
    """Scholar citation metrics."""
    h_index: int = 0
    i10_index: int = 0
    total_citations: int = 0


class ProfessorProfile(BaseModel):
    """Professor profile with research information."""
    id: UUID
    name: str
    title: str | None = None
    department: str | None = None
    university: str
    email: str | None = None
    openalex_id: str | None = None
    google_scholar_url: str | None = None
    directory_url: str | None = None
    website: str | None = None
    research_areas: list[str] = []
    publications: list[Publication] = []
    citation_metrics: CitationMetrics | None = None
    last_updated: datetime


class Education(BaseModel):
    """Education entry from CV."""
    institution: str
    degree: str
    field: str | None = None
    year: int | None = None


class Experience(BaseModel):
    """Work/research experience entry."""
    organization: str
    role: str
    description: str | None = None
    start_year: int | None = None
    end_year: int | None = None


class StudentProfile(BaseModel):
    """Student profile extracted from CV and inputs."""
    session_id: UUID
    stated_interests: list[str] = []
    education: list[Education] = []
    experience: list[Experience] = []
    publications: list[Publication] = []
    skills: list[str] = []
    extracted_keywords: list[str] = []


class MatchResult(BaseModel):
    """Professor match result with explanation."""
    professor: ProfessorProfile
    match_score: float
    alignment_reasons: list[str]
    relevant_publications: list[Publication] = []
    shared_keywords: list[str] = []
    recommendation_text: str


class MatchRequest(BaseModel):
    """Request to start matching process."""
    session_id: str
    university: str
    research_interests: list[str]
    file_ids: list[str] = []


class MatchStatusResponse(BaseModel):
    """Match progress status."""
    match_id: str
    status: str
    progress: int
    current_step: str | None = None
    elapsed_time: float | None = None


class MatchResultsResponse(BaseModel):
    """Match results response."""
    match_id: str
    status: str
    results: list[MatchResult] = []
    total_time: float | None = None


class SessionResponse(BaseModel):
    """Session creation response."""
    session_id: str


class SessionData(BaseModel):
    """Session data response."""
    session_id: str
    university: str | None = None
    research_interests: list[str] = []
    file_ids: list[str] = []
    status: str = "created"


class UploadResponse(BaseModel):
    """File upload response."""
    file_id: str
    filename: str


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str


class CleanupResponse(BaseModel):
    """Cleanup endpoint response."""
    message: str
    sessions_cleaned: int
    ttl_hours: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str


# --- Auth schemas ---

class SignupRequest(BaseModel):
    """User signup request."""
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1)
    session_id: str | None = None


class LoginRequest(BaseModel):
    """User login request."""
    email: EmailStr
    password: str
    session_id: str | None = None


class ForgotPasswordRequest(BaseModel):
    """Forgot password request."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password request."""
    token: str
    new_password: str = Field(min_length=8)


class UserResponse(BaseModel):
    """User info returned in auth responses."""
    id: str
    email: str
    name: str
    created_at: datetime


class AuthResponse(BaseModel):
    """Auth response with token."""
    user: UserResponse
    access_token: str
    token_type: str = "bearer"


# --- Search history schemas ---

class SearchHistorySummary(BaseModel):
    """Summary of a saved search (for listing)."""
    id: str
    match_id: str
    university: str
    research_interests: list[str] = []
    result_count: int
    created_at: datetime


class SearchHistoryDetail(BaseModel):
    """Full search with results."""
    id: str
    match_id: str
    university: str
    research_interests: list[str] = []
    results: list[MatchResult] = []
    total_time: float | None = None
    created_at: datetime


# --- Search credits schemas ---

class SearchUsageItem(BaseModel):
    """Single credit usage entry."""
    match_id: str
    university: str
    created_at: datetime


class CreditsResponse(BaseModel):
    """User credit balance and usage."""
    balance: int
    next_free_credit_at: datetime | None = None
    usage_history: list[SearchUsageItem] = []


class PlanInfo(BaseModel):
    """Credit purchase plan."""
    id: str
    name: str
    credits: int
    price_usd: float
    available: bool = False


class PlansResponse(BaseModel):
    """Available credit plans."""
    plans: list[PlanInfo]
    message: str = "Credit purchases coming soon!"
