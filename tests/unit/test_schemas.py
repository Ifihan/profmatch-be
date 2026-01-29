"""Unit tests for app/models/schemas.py Pydantic models."""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    Publication,
    CitationMetrics,
    ProfessorProfile,
    Education,
    Experience,
    StudentProfile,
    MatchResult,
)


class TestPublication:
    """Tests for Publication model."""

    def test_minimal_publication(self):
        """Publication with only required fields."""
        pub = Publication(
            title="Test Paper",
            authors=["Author One"],
            year=2023,
        )
        assert pub.title == "Test Paper"
        assert pub.authors == ["Author One"]
        assert pub.year == 2023
        assert pub.venue is None
        assert pub.abstract is None
        assert pub.citation_count == 0
        assert pub.url is None

    def test_full_publication(self):
        """Publication with all fields."""
        pub = Publication(
            title="Test Paper",
            authors=["Author One", "Author Two"],
            year=2023,
            venue="NeurIPS",
            abstract="This is an abstract",
            citation_count=100,
            url="https://example.com/paper",
        )
        assert pub.venue == "NeurIPS"
        assert pub.citation_count == 100

    def test_empty_authors_allowed(self):
        """Empty authors list is allowed."""
        pub = Publication(title="Test", authors=[], year=2023)
        assert pub.authors == []


class TestCitationMetrics:
    """Tests for CitationMetrics model."""

    def test_default_values(self):
        """CitationMetrics has sensible defaults."""
        metrics = CitationMetrics()
        assert metrics.h_index == 0
        assert metrics.i10_index == 0
        assert metrics.total_citations == 0

    def test_with_values(self):
        """CitationMetrics with custom values."""
        metrics = CitationMetrics(h_index=25, i10_index=40, total_citations=5000)
        assert metrics.h_index == 25
        assert metrics.i10_index == 40
        assert metrics.total_citations == 5000


class TestProfessorProfile:
    """Tests for ProfessorProfile model."""

    def test_minimal_profile(self):
        """Profile with only required fields."""
        profile = ProfessorProfile(
            id=uuid4(),
            name="Dr. Test",
            university="MIT",
            last_updated=datetime.utcnow(),
        )
        assert profile.name == "Dr. Test"
        assert profile.university == "MIT"
        assert profile.title is None
        assert profile.research_areas == []
        assert profile.publications == []

    def test_full_profile(self):
        """Profile with all fields."""
        now = datetime.utcnow()
        profile = ProfessorProfile(
            id=uuid4(),
            name="Dr. Test",
            title="Professor",
            department="Computer Science",
            university="MIT",
            email="test@mit.edu",
            scholar_id="ABC123",
            google_scholar_url="https://scholar.google.com/...",
            research_areas=["AI", "ML"],
            publications=[Publication(title="Paper", authors=["Dr. Test"], year=2023)],
            citation_metrics=CitationMetrics(h_index=25),
            last_updated=now,
        )
        assert profile.email == "test@mit.edu"
        assert len(profile.publications) == 1
        assert profile.citation_metrics.h_index == 25

    def test_invalid_email_rejected(self):
        """Invalid email format is rejected."""
        with pytest.raises(ValidationError):
            ProfessorProfile(
                id=uuid4(),
                name="Dr. Test",
                university="MIT",
                email="not-an-email",
                last_updated=datetime.utcnow(),
            )


class TestEducation:
    """Tests for Education model."""

    def test_minimal_education(self):
        """Education with required fields only."""
        edu = Education(institution="MIT", degree="PhD")
        assert edu.institution == "MIT"
        assert edu.degree == "PhD"
        assert edu.field is None
        assert edu.year is None

    def test_full_education(self):
        """Education with all fields."""
        edu = Education(
            institution="MIT",
            degree="PhD",
            field="Computer Science",
            year=2020,
        )
        assert edu.field == "Computer Science"
        assert edu.year == 2020


class TestExperience:
    """Tests for Experience model."""

    def test_minimal_experience(self):
        """Experience with required fields only."""
        exp = Experience(organization="Google", role="Intern")
        assert exp.organization == "Google"
        assert exp.role == "Intern"

    def test_full_experience(self):
        """Experience with all fields."""
        exp = Experience(
            organization="Google",
            role="Research Scientist",
            description="Worked on ML models",
            start_year=2018,
            end_year=2022,
        )
        assert exp.start_year == 2018
        assert exp.end_year == 2022


class TestStudentProfile:
    """Tests for StudentProfile model."""

    def test_minimal_profile(self):
        """Student profile with only session_id."""
        profile = StudentProfile(session_id=uuid4())
        assert profile.stated_interests == []
        assert profile.education == []
        assert profile.skills == []

    def test_full_profile(self):
        """Student profile with all fields."""
        profile = StudentProfile(
            session_id=uuid4(),
            stated_interests=["AI", "ML"],
            education=[Education(institution="MIT", degree="BS")],
            experience=[Experience(organization="Google", role="Intern")],
            publications=[Publication(title="Paper", authors=["Me"], year=2023)],
            skills=["Python", "TensorFlow"],
            extracted_keywords=["deep learning", "NLP"],
        )
        assert len(profile.education) == 1
        assert len(profile.skills) == 2


class TestMatchResult:
    """Tests for MatchResult model."""

    def test_match_result(self):
        """Complete match result."""
        professor = ProfessorProfile(
            id=uuid4(),
            name="Dr. Test",
            university="MIT",
            last_updated=datetime.utcnow(),
        )
        match = MatchResult(
            professor=professor,
            match_score=85.5,
            alignment_reasons=["Same research area", "Published together"],
            relevant_publications=[],
            shared_keywords=["AI", "ML"],
            recommendation_text="Great match!",
        )
        assert match.match_score == 85.5
        assert len(match.alignment_reasons) == 2
        assert match.recommendation_text == "Great match!"

    def test_match_score_range(self):
        """Match score can be any float (no validation)."""
        professor = ProfessorProfile(
            id=uuid4(),
            name="Dr. Test",
            university="MIT",
            last_updated=datetime.utcnow(),
        )
        # Note: schema doesn't enforce 0-100 range
        match = MatchResult(
            professor=professor,
            match_score=150.0,  # Out of typical range
            alignment_reasons=[],
            shared_keywords=[],
            recommendation_text="",
        )
        assert match.match_score == 150.0
