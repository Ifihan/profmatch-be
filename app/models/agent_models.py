"""Pydantic output models for Gemini structured output (response_schema)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CVEducation(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str | None = None
    year: int | None = None


class CVExperience(BaseModel):
    organization: str = ""
    role: str = ""
    description: str | None = None
    start_year: int | None = None
    end_year: int | None = None


class CVPublication(BaseModel):
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None


class ParsedCV(BaseModel):
    """Structured CV data extracted by Gemini."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    education: list[CVEducation] = Field(default_factory=list)
    experience: list[CVExperience] = Field(default_factory=list)
    publications: list[CVPublication] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    research_interests: list[str] = Field(default_factory=list)


class FacultyMember(BaseModel):
    """A single faculty member extracted from a web page."""

    name: str
    title: str | None = None
    department: str | None = None
    email: str | None = None
    profile_url: str | None = None


class ProfessorPageOutput(BaseModel):
    """Professor details extracted from their profile page."""

    name: str | None = None
    title: str | None = None
    department: str | None = None
    email: str | None = None
    research_areas: list[str] = Field(default_factory=list)
    bio: str | None = None


class FilterOutput(BaseModel):
    """Indices of selected faculty members."""

    selected_indices: list[int] = Field(default_factory=list)


class ResearchAreasOutput(BaseModel):
    """Research areas extracted from publication titles."""

    areas: list[str] = Field(default_factory=list)


class MatchEntry(BaseModel):
    """A single professor match."""

    professor_id: str
    match_score: float = Field(ge=0, le=100)
    alignment_reasons: list[str] = Field(default_factory=list)
    relevant_publication_titles: list[str] = Field(default_factory=list)
    shared_keywords: list[str] = Field(default_factory=list)
    recommendation_text: str = ""


class MatchOutput(BaseModel):
    """Ranked list of professor matches."""

    matches: list[MatchEntry] = Field(default_factory=list)
