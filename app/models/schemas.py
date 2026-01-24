from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


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
    email: EmailStr | None = None
    scholar_id: str | None = None
    google_scholar_url: str | None = None
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
