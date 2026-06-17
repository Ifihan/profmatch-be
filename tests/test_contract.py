"""Validates the exact MatchResult / MatchResultsResponse contract from the
existing system against our Pydantic schemas."""
from app.schemas.match import MatchResult, MatchResultsResponse

SAMPLE = {
  "professor": {
    "id": "prof_abc123", "name": "Dr. Jane Smith", "title": "Associate Professor",
    "department": "Computer Science", "university": "MIT", "email": "jsmith@mit.edu",
    "scholar_id": "abc123xyz",
    "research_areas": ["machine learning", "natural language processing"],
    "publications": [{
      "title": "Transformers in Low-Resource Settings",
      "authors": ["Jane Smith", "John Doe"], "year": 2023, "venue": "NeurIPS",
      "abstract": "We explore...", "citation_count": 42, "url": "https://arxiv.org/abs/xxxx"
    }],
    "citation_metrics": {"h_index": 18, "total_citations": 3400},
    "last_updated": "2024-11-01T00:00:00Z"
  },
  "match_score": 0.87,
  "alignment_reasons": ["Both focus on NLP and low-resource learning",
                        "Shared interest in transformer architectures"],
  "relevant_publications": [{
      "title": "Transformers in Low-Resource Settings",
      "authors": ["Jane Smith", "John Doe"], "year": 2023, "venue": "NeurIPS",
      "abstract": "We explore...", "citation_count": 42, "url": "https://arxiv.org/abs/xxxx"
  }],
  "shared_keywords": ["transformers", "NLP", "low-resource"],
  "recommendation_text": "Dr. Smith's work closely aligns with your interest in NLP..."
}


def test_match_result_accepts_canonical_object():
    mr = MatchResult.model_validate(SAMPLE)
    assert mr.professor.name == "Dr. Jane Smith"
    assert mr.match_score == 0.87


def test_round_trip_keys_match():
    dumped = MatchResult.model_validate(SAMPLE).model_dump(mode="json")
    assert set(dumped.keys()) == set(SAMPLE.keys())
    assert set(dumped["professor"].keys()) == set(SAMPLE["professor"].keys())


def test_wrapper_response():
    mr = MatchResult.model_validate(SAMPLE)
    resp = MatchResultsResponse(
        session_id="sess_xyz", matches=[mr],
        total_professors_analyzed=120, processing_time_seconds=4.7,
    )
    assert resp.total_professors_analyzed == 120


def test_optional_fields_omittable():
    slim = {
        "professor": {"id": "prof_x", "name": "Dr. No Email",
                      "research_areas": [], "publications": []},
        "match_score": 0.5,
    }
    mr = MatchResult.model_validate(slim)
    assert mr.professor.email is None
    assert mr.professor.citation_metrics is None
