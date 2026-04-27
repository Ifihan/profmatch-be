from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models import CitationMetrics, ProfessorProfile, Publication
from app.services import gemini
from app.services.matching.faculty import (
    _fetch_faculty_openalex,
    _is_allowed_directory_url,
)
from app.services.matching.ranking import _deterministic_fallback_matches


class _FakeResponse:
    def __init__(self, parsed, text=""):
        self.parsed = parsed
        self.text = text


class _FakeModels:
    async def generate_content(self, **kwargs):
        return _FakeResponse(parsed=[], text="[]")


class _FakeAio:
    models = _FakeModels()


class _FakeClient:
    aio = _FakeAio()


@pytest.mark.asyncio
async def test_extract_faculty_accepts_valid_empty_list(monkeypatch):
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient())
    result = await gemini.extract_faculty("No faculty listed here.", "https://example.edu/page")
    assert result == []


def test_directory_url_filter_rejects_private_and_non_html_urls():
    allowed_hosts = {"rwth-aachen.de"}
    assert not _is_allowed_directory_url(
        url="http://192.168.100.1/housekeep.asp",
        allowed_hosts=allowed_hosts,
    )
    assert not _is_allowed_directory_url(
        url="http://dagstuhl.sunsite.rwth-aachen.de/volltexte/2020/paper.pdf",
        allowed_hosts=allowed_hosts,
    )
    assert not _is_allowed_directory_url(
        url="https://example.com/faculty",
        allowed_hosts=allowed_hosts,
    )
    assert _is_allowed_directory_url(
        url="https://www.informatik.rwth-aachen.de/cms/informatik/people/",
        allowed_hosts=allowed_hosts,
    )


@pytest.mark.asyncio
async def test_openalex_discovery_prefers_relevant_authors(monkeypatch):
    async def fake_resolve_institution(*, query: str):
        return {
            "id": "https://openalex.org/I1",
            "display_name": "Test University",
        }

    async def fake_get_authors_by_institution(*, institution_id: str, topics=None, limit: int = 50):
        return [
            {
                "name": "Irrelevant Materials Researcher",
                "openalex_id": "A1",
                "topics": ["Corrosion", "Batteries"],
                "topic_details": [
                    {
                        "name": "Corrosion",
                        "subfield": "Materials Engineering",
                        "field": "Engineering",
                        "domain": "Physical Sciences",
                    }
                ],
                "h_index": 30,
                "i10_index": 40,
                "cited_by_count": 10000,
                "works_count": 300,
                "orcid": None,
                "last_known_institutions": [{"id": institution_id}],
            },
            {
                "name": "Relevant Systems Researcher",
                "openalex_id": "A2",
                "topics": ["Distributed Systems", "Software Engineering"],
                "topic_details": [
                    {
                        "name": "Distributed Systems",
                        "subfield": "Computer Science",
                        "field": "Computer Science",
                        "domain": "Technology",
                    }
                ],
                "h_index": 10,
                "i10_index": 12,
                "cited_by_count": 200,
                "works_count": 50,
                "orcid": None,
                "last_known_institutions": [{"id": institution_id}],
            },
        ]

    monkeypatch.setattr("app.services.openalex.resolve_institution", fake_resolve_institution)
    monkeypatch.setattr("app.services.openalex.get_authors_by_institution", fake_get_authors_by_institution)

    faculty, warnings, institution_name = await _fetch_faculty_openalex(
        university="https://www.example.edu",
        research_interests=["software engineering", "distributed systems"],
    )

    assert institution_name == "Test University"
    assert warnings == []
    assert faculty[0]["name"] == "Relevant Systems Researcher"


def test_fallback_ranker_uses_normalized_keywords():
    relevant_professor = ProfessorProfile(
        id=uuid4(),
        name="Relevant Researcher",
        university="Example University",
        research_areas=["Distributed Systems", "Cloud Reliability"],
        publications=[
            Publication(
                title="Reliable Distributed Systems",
                authors=["A. Author"],
                year=2025,
                citation_count=10,
            )
        ],
        citation_metrics=CitationMetrics(h_index=5, i10_index=4, total_citations=100),
        last_updated=datetime.now(UTC),
    )
    irrelevant_professor = ProfessorProfile(
        id=uuid4(),
        name="Irrelevant Researcher",
        university="Example University",
        research_areas=["Corrosion Science"],
        publications=[],
        citation_metrics=CitationMetrics(h_index=10, i10_index=8, total_citations=500),
        last_updated=datetime.now(UTC),
    )

    matches = _deterministic_fallback_matches(
        professors=[irrelevant_professor, relevant_professor],
        base_scores={
            str(irrelevant_professor.id): 1.0,
            str(relevant_professor.id): 1.0,
        },
        research_interests=["software engineering"],
        research_keywords=["distributed systems", "cloud reliability"],
    )

    assert matches[0].professor.name == "Relevant Researcher"
