"""Unit tests for app/services/openalex.py."""

import pytest

from app.services.openalex import (
    _extract_domain_from_query,
    _parse_author,
    _parse_institution,
    _parse_work,
    _reconstruct_abstract,
)


class TestExtractDomainFromQuery:
    """Tests for _extract_domain_from_query helper."""

    def test_plain_domain(self):
        assert _extract_domain_from_query("mit.edu") == "mit.edu"

    def test_full_url(self):
        assert _extract_domain_from_query("https://www.mit.edu/cs") == "mit.edu"

    def test_url_with_http(self):
        assert _extract_domain_from_query("http://stanford.edu/faculty") == "stanford.edu"

    def test_plain_name_returns_none(self):
        assert _extract_domain_from_query("Massachusetts Institute of Technology") is None

    def test_empty_returns_none(self):
        assert _extract_domain_from_query("") is None

    def test_domain_with_www(self):
        assert _extract_domain_from_query("www.ox.ac.uk") == "ox.ac.uk"


class TestReconstructAbstract:
    """Tests for _reconstruct_abstract helper."""

    def test_none_returns_none(self):
        assert _reconstruct_abstract(None) is None

    def test_empty_dict_returns_none(self):
        assert _reconstruct_abstract({}) is None

    def test_simple_reconstruction(self):
        inverted_index = {"the": [0], "cat": [1], "sat": [2]}
        assert _reconstruct_abstract(inverted_index) == "the cat sat"

    def test_repeated_words(self):
        inverted_index = {"the": [0, 2], "cat": [1], "sat": [3]}
        assert _reconstruct_abstract(inverted_index) == "the cat the sat"


class TestParseInstitution:
    """Tests for _parse_institution helper."""

    def test_full_institution(self):
        raw = {
            "id": "https://openalex.org/I123",
            "display_name": "MIT",
            "ror": "https://ror.org/042nb2s44",
            "country_code": "US",
            "type": "education",
            "works_count": 500000,
            "cited_by_count": 10000000,
        }
        result = _parse_institution(raw)
        assert result["id"] == "https://openalex.org/I123"
        assert result["display_name"] == "MIT"
        assert result["country_code"] == "US"

    def test_minimal_institution(self):
        result = _parse_institution({})
        assert result["id"] == ""
        assert result["display_name"] == ""
        assert result["works_count"] == 0


class TestParseAuthor:
    """Tests for _parse_author helper."""

    def test_full_author(self):
        raw = {
            "id": "https://openalex.org/A123",
            "display_name": "Jane Doe",
            "ids": {"orcid": "0000-0001-2345-6789"},
            "summary_stats": {"h_index": 25, "i10_index": 40},
            "cited_by_count": 5000,
            "works_count": 100,
            "topics": [
                {
                    "display_name": "Machine Learning",
                    "subfield": {"display_name": "Artificial Intelligence"},
                    "field": {"display_name": "Computer Science"},
                    "domain": {"display_name": "Technology"},
                },
            ],
        }
        result = _parse_author(raw)
        assert result["name"] == "Jane Doe"
        assert result["h_index"] == 25
        assert result["i10_index"] == 40
        assert result["cited_by_count"] == 5000
        assert "Machine Learning" in result["topics"]
        assert result["topic_details"][0]["subfield"] == "Artificial Intelligence"
        assert result["orcid"] == "0000-0001-2345-6789"

    def test_minimal_author(self):
        result = _parse_author({})
        assert result["name"] == ""
        assert result["h_index"] == 0
        assert result["topics"] == []


class TestParseWork:
    """Tests for _parse_work helper."""

    def test_full_work(self):
        raw = {
            "id": "https://openalex.org/W123",
            "title": "Deep Learning Paper",
            "publication_year": 2024,
            "cited_by_count": 50,
            "doi": "https://doi.org/10.1234/test",
            "abstract_inverted_index": {"deep": [0], "learning": [1], "is": [2], "great": [3]},
            "primary_location": {
                "source": {"display_name": "NeurIPS"},
            },
            "authorships": [
                {"author": {"display_name": "Jane Doe"}},
                {"author": {"display_name": "John Smith"}},
            ],
            "topics": [
                {"display_name": "Deep Learning"},
                {"display_name": "Neural Networks"},
            ],
        }
        result = _parse_work(raw)
        assert result["title"] == "Deep Learning Paper"
        assert result["abstract"] == "deep learning is great"
        assert result["citation_count"] == 50
        assert result["venue"] == "NeurIPS"
        assert result["authors"] == ["Jane Doe", "John Smith"]
        assert "Deep Learning" in result["topics"]

    def test_work_without_abstract(self):
        raw = {"id": "W1", "title": "Paper", "cited_by_count": 0}
        result = _parse_work(raw)
        assert result["abstract"] is None
        assert result["authors"] == []
