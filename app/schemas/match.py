from datetime import datetime
from pydantic import BaseModel


class Publication(BaseModel):
    title: str
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    citation_count: int | None = None
    url: str | None = None


class CitationMetrics(BaseModel):
    h_index: int | None = None
    total_citations: int | None = None


class Professor(BaseModel):
    id: str
    name: str
    title: str | None = None
    department: str | None = None
    university: str | None = None
    email: str | None = None
    scholar_id: str | None = None
    research_areas: list[str] = []
    publications: list[Publication] = []
    citation_metrics: CitationMetrics | None = None
    last_updated: datetime | None = None


class MatchResult(BaseModel):
    professor: Professor
    match_score: float  # 0.0 - 1.0
    alignment_reasons: list[str] = []
    relevant_publications: list[Publication] = []
    shared_keywords: list[str] = []
    recommendation_text: str = ""


class MatchResultsResponse(BaseModel):
    session_id: str
    matches: list[MatchResult]
    total_professors_analyzed: int
    processing_time_seconds: float


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    result: MatchResultsResponse | None = None  # populated once status == "done"
    error: str | None = None


class SearchDetailResponse(JobStatusResponse):
    """Search-history detail: status envelope plus the original request fields."""
    university_url: str | None = None
    research_interests: str | None = None
    created_at: datetime
