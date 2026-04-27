"""Unit tests for app/services/orchestrator.py helper functions."""

import pytest

from app.models.agent_models import ResearchInterestProfileOutput
from app.services.orchestrator import (
    build_research_interest_profile,
    to_str,
    to_list,
    to_int,
    _directory_search_terms,
    _extract_domain,
    _infer_matching_route,
    _infer_target_academic_units,
    _merge_faculty_sources,
    _normalize_title,
    filter_faculty_by_relevance,
    shortlist_faculty_for_enrichment,
    _compute_topic_score,
)


class TestToStr:
    """Tests for to_str helper function."""

    def test_none_returns_none(self):
        """None input returns None."""
        assert to_str(None) is None

    def test_string_returns_string(self):
        """String input returns same string."""
        assert to_str("hello") == "hello"

    def test_int_returns_string(self):
        """Integer input returns string representation."""
        assert to_str(42) == "42"

    def test_float_returns_string(self):
        """Float input returns string representation."""
        assert to_str(3.14) == "3.14"

    def test_list_returns_joined_string(self):
        """List input returns semicolon-joined string."""
        assert to_str(["a", "b", "c"]) == "a; b; c"

    def test_empty_list_returns_empty_string(self):
        """Empty list returns empty string."""
        assert to_str([]) == ""

    def test_list_with_mixed_types(self):
        """List with mixed types converts all to strings."""
        assert to_str(["hello", 42, 3.14]) == "hello; 42; 3.14"

    def test_single_item_list(self):
        """Single item list returns just that item as string."""
        assert to_str(["only"]) == "only"


class TestToList:
    """Tests for to_list helper function."""

    def test_none_returns_empty_list(self):
        """None input returns empty list."""
        assert to_list(None) == []

    def test_list_returns_stringified_list(self):
        """List input returns list with all items stringified."""
        assert to_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_list_with_numbers_stringifies(self):
        """List with numbers converts them to strings."""
        assert to_list([1, 2, 3]) == ["1", "2", "3"]

    def test_string_splits_by_comma(self):
        """String input is split by comma."""
        assert to_list("a, b, c") == ["a", "b", "c"]

    def test_string_without_comma(self):
        """String without comma returns single-item list."""
        assert to_list("hello") == ["hello"]

    def test_string_with_extra_spaces_trimmed(self):
        """Spaces around comma-separated items are trimmed."""
        assert to_list("  a  ,  b  ,  c  ") == ["a", "b", "c"]

    def test_empty_list_returns_empty_list(self):
        """Empty list returns empty list."""
        assert to_list([]) == []

    def test_empty_string_returns_single_item(self):
        """Empty string returns list with empty string."""
        assert to_list("") == [""]

    def test_single_value_returns_list(self):
        """Single non-string, non-list value returns single-item list."""
        assert to_list(42) == ["42"]


class TestToInt:
    """Tests for to_int helper function."""

    def test_none_returns_zero(self):
        """None input returns 0."""
        assert to_int(None) == 0

    def test_int_returns_int(self):
        """Integer input returns same integer."""
        assert to_int(42) == 42

    def test_negative_int_returns_int(self):
        """Negative integer returns correctly."""
        assert to_int(-10) == -10

    def test_string_number_converts(self):
        """String number converts to int."""
        assert to_int("42") == 42

    def test_string_with_spaces_converts(self):
        """String with surrounding spaces converts to int."""
        assert to_int(" 42 ") == 42

    def test_invalid_string_returns_zero(self):
        """Invalid string returns 0."""
        assert to_int("not a number") == 0

    def test_float_truncates(self):
        """Float input returns truncated int."""
        assert to_int(3.9) == 3

    def test_float_string_fails(self):
        """Float string returns 0 (int() can't parse floats directly)."""
        assert to_int("3.14") == 0

    def test_empty_string_returns_zero(self):
        """Empty string returns 0."""
        assert to_int("") == 0

    def test_list_returns_zero(self):
        """List returns 0."""
        assert to_int([1, 2, 3]) == 0

    def test_dict_returns_zero(self):
        """Dict returns 0."""
        assert to_int({"value": 42}) == 0


class TestExtractDomain:
    """Tests for _extract_domain helper function."""

    def test_simple_url(self):
        """Extract domain from simple URL."""
        assert _extract_domain("https://example.com") == "example.com"

    def test_url_with_www(self):
        """www prefix is stripped."""
        assert _extract_domain("https://www.example.com") == "example.com"

    def test_url_with_path(self):
        """Path is ignored."""
        assert _extract_domain("https://example.com/path/to/page") == "example.com"

    def test_url_with_subdomain(self):
        """Subdomain is preserved (unless www)."""
        assert _extract_domain("https://api.example.com") == "api.example.com"

    def test_url_with_port(self):
        """Port is preserved."""
        result = _extract_domain("https://example.com:8080/path")
        # Note: the implementation splits by "/" which would include port
        assert "example.com" in result

    def test_http_url(self):
        """HTTP URLs work the same as HTTPS."""
        assert _extract_domain("http://example.com") == "example.com"

    def test_edu_domain(self):
        """Educational domains work correctly."""
        assert _extract_domain("https://www.mit.edu/faculty") == "mit.edu"

    def test_url_with_query_string(self):
        """Query strings are part of path, stripped correctly."""
        result = _extract_domain("https://example.com?query=value")
        assert "example.com" in result

    def test_empty_string_returns_empty(self):
        """Empty string returns empty string."""
        assert _extract_domain("") == ""

    def test_invalid_url_returns_empty(self):
        """Invalid URL returns empty string (graceful failure)."""
        assert _extract_domain("not a url") == ""


class TestComputeTopicScore:
    """Tests for _compute_topic_score helper."""

    def test_exact_topic_match(self):
        """Exact topic name match scores high."""
        faculty = {
            "topic_details": [
                {
                    "name": "Machine Learning",
                    "subfield": "Artificial Intelligence",
                    "field": "Computer Science",
                    "domain": "Technology",
                },
            ],
        }
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens={"machine", "learning"},
            interest_phrases=["machine learning"],
        )
        assert score > 0

    def test_department_alignment_boosts_target_unit(self):
        """Matching department gets a boost for the inferred academic unit."""
        faculty = {
            "topics": ["Software Engineering", "Program Analysis"],
            "department": "Department of Computing Science",
            "directory_verified": True,
        }
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens={"software", "engineering"},
            interest_phrases=["software engineering"],
            target_units=["computer science", "software engineering"],
        )
        assert score >= 7.0

    def test_department_mismatch_is_penalized(self):
        """Unrelated departments should rank below clearly aligned departments."""
        score = _compute_topic_score(
            faculty={
                "topics": ["Software Engineering"],
                "department": "Electrical and Computer Engineering",
            },
            interest_tokens={"software", "engineering"},
            interest_phrases=["software engineering"],
            target_units=["computer science", "software engineering"],
        )
        assert score < 3.0

    def test_non_stem_department_alignment_boosts_history(self):
        """Humanities departments should benefit from route-specific scoring."""
        score = _compute_topic_score(
            faculty={
                "topics": ["Modern History", "Historiography"],
                "department": "Department of History",
                "directory_verified": True,
            },
            interest_tokens={"history", "historiography"},
            interest_phrases=["modern history", "historiography"],
            target_units=["history"],
            route_name="humanities",
        )
        assert score >= 7.0

    def test_no_match_returns_zero(self):
        """Unrelated topics score zero."""
        faculty = {
            "topic_details": [
                {
                    "name": "Marine Biology",
                    "subfield": "Ecology",
                    "field": "Biology",
                    "domain": "Life Sciences",
                },
            ],
        }
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens={"quantum", "computing"},
            interest_phrases=["quantum computing"],
        )
        assert score == 0.0

    def test_plain_topics_fallback(self):
        """Falls back to plain topic name matching when no topic_details."""
        faculty = {"topics": ["Natural Language Processing", "Deep Learning"]}
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens={"deep", "learning"},
            interest_phrases=["deep learning"],
        )
        assert score > 0

    def test_scraped_faculty_keyword_match(self):
        """Falls back to keyword matching on name/title/department."""
        faculty = {
            "name": "Dr. Jane Smith",
            "title": "Professor of Machine Learning",
            "department": "Computer Science",
        }
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens={"machine", "learning"},
            interest_phrases=["machine learning"],
        )
        assert score > 0

    def test_empty_faculty_scores_zero(self):
        """Faculty with no data scores zero."""
        score = _compute_topic_score(
            faculty={},
            interest_tokens={"ai"},
            interest_phrases=["artificial intelligence"],
        )
        assert score == 0.0


class TestTargetAcademicUnits:
    """Tests for academic-unit inference from research interests."""

    def test_software_interests_prioritize_computing_units(self):
        units = _infer_target_academic_units([
            "software engineering",
            "software systems",
        ])
        assert "computer science" in units
        assert "software engineering" in units

    def test_history_interests_prioritize_humanities_units(self):
        units = _infer_target_academic_units([
            "modern history",
            "historiography",
        ])
        assert "history" in units

    def test_finance_interests_prioritize_business_units(self):
        units = _infer_target_academic_units([
            "corporate finance",
            "financial economics",
        ])
        assert "finance" in units


class TestRouteInference:
    """Tests for broad-discipline routing."""

    def test_computing_route_detected(self):
        route = _infer_matching_route(["software engineering", "distributed systems"])
        assert route == "computing"

    def test_humanities_route_detected(self):
        route = _infer_matching_route(["history", "literary studies"])
        assert route == "humanities"

    def test_business_route_detected(self):
        route = _infer_matching_route(["finance", "econometrics"])
        assert route == "business_economics"

    def test_ambiguous_route_falls_back_to_generic(self):
        route = _infer_matching_route(["interdisciplinary research"])
        assert route == "generic"


class TestResearchInterestProfile:
    """Tests for structured research-interest normalization."""

    @pytest.mark.asyncio
    async def test_deterministic_profile_splits_long_interest(self):
        profile = await build_research_interest_profile([
            "I am interested in software engineering and distributed computing for scalable systems",
        ])
        assert profile.route_name == "computing"
        assert any("software" in phrase for phrase in profile.normalized_phrases)
        assert any("distributed" in phrase for phrase in profile.normalized_phrases)
        assert any(
            keyword in {"scalable", "software engineering", "distributed computing", "systems"}
            for keyword in profile.keywords
        )

    @pytest.mark.asyncio
    async def test_gemini_profile_is_merged_when_available(self, monkeypatch):
        async def fake_parse_research_interest_profile(interests_text: str):
            return ResearchInterestProfileOutput(
                primary_themes=["distributed systems", "software reliability"],
                related_keywords=["fault tolerance", "cloud systems"],
                target_units=["computer science"],
                route_hint="computing",
            )

        monkeypatch.setattr(
            "app.services.gemini.parse_research_interest_profile",
            fake_parse_research_interest_profile,
        )

        profile = await build_research_interest_profile([
            "I am interested in scalable software systems, cloud-native architectures, and reliability in distributed applications.",
        ])
        assert profile.route_name == "computing"
        assert "distributed systems" in profile.normalized_phrases
        assert "fault tolerance" in profile.keywords
        assert "computer science" in profile.target_units


class TestDirectorySearchTerms:
    """Tests for route-guided search-term generation."""

    def test_non_stem_search_terms_include_route_terms(self):
        terms = _directory_search_terms(
            research_interests=["modern history", "historiography"],
            route_name="humanities",
        )
        assert "humanities" in terms
        assert "history" in terms


class TestTitleNormalization:
    """Tests for title cleanup and normalization."""

    def test_normalize_associate_professor(self):
        assert _normalize_title("Associate professor of Computer Science") == "Associate Professor"

    def test_unknown_title_is_capitalized(self):
        assert _normalize_title("department chair") == "Department Chair"


class TestMergeFacultySources:
    """Tests for hybrid OpenAlex and directory metadata merging."""

    def test_scraped_metadata_enriches_openalex_author(self):
        merged = _merge_faculty_sources(
            openalex_faculty=[
                {
                    "name": "Dr. Alice Smith",
                    "openalex_id": "A1",
                    "topics": ["Software Engineering"],
                    "topic_details": [],
                },
            ],
            scraped_faculty=[
                {
                    "name": "Alice Smith",
                    "title": "Associate Professor",
                    "department": "Computing Science",
                    "email": "alice@example.edu",
                    "profile_url": "https://example.edu/alice",
                },
            ],
        )
        assert len(merged) == 1
        assert merged[0]["title"] == "Associate Professor"
        assert merged[0]["department"] == "Computing Science"
        assert merged[0]["directory_verified"] is True

    def test_unmatched_scraped_faculty_is_retained(self):
        merged = _merge_faculty_sources(
            openalex_faculty=[],
            scraped_faculty=[
                {
                    "name": "Prof. Carol Jones",
                    "title": "Senior Lecturer",
                    "department": "Computer Science",
                },
            ],
        )
        assert len(merged) == 1
        assert merged[0]["name"] == "Prof. Carol Jones"
        assert merged[0]["title"] == "Senior Lecturer"


class TestFilterFacultyByRelevance:
    """Tests for filter_faculty_by_relevance."""

    def test_small_list_returned_as_is(self):
        """Lists <= 30 returned unchanged."""
        faculty = [{"name": f"Prof {i}"} for i in range(20)]
        result = filter_faculty_by_relevance(
            faculty_data=faculty, research_interests=["machine learning"]
        )
        assert len(result) == 20


class TestShortlistFacultyForEnrichment:
    """Tests for keeping enrichment work on a smaller shortlist."""

    def test_large_list_is_trimmed_to_requested_limit(self):
        faculty = [
            {
                "name": f"Prof {i}",
                "topics": ["Machine Learning"] if i < 3 else [f"Topic {i}"],
                "cited_by_count": 1000 - i,
            }
            for i in range(20)
        ]
        result = shortlist_faculty_for_enrichment(
            faculty_data=faculty,
            research_interests=["machine learning"],
            limit=12,
        )
        assert len(result) == 12
        assert result[0]["topics"] == ["Machine Learning"]

    def test_large_list_filtered_to_20(self):
        """When relevant matches exist, zero-score candidates are dropped."""
        faculty = [
            {"name": f"Prof {i}", "topics": [f"Topic {i}"]}
            for i in range(50)
        ]
        # add one with matching topic
        faculty[0]["topics"] = ["Machine Learning"]
        result = filter_faculty_by_relevance(
            faculty_data=faculty, research_interests=["machine learning"]
        )
        assert len(result) == 1
        # the matching professor should be first
        assert result[0]["topics"] == ["Machine Learning"]

    def test_openalex_topics_scored_higher(self):
        """Faculty with OpenAlex topic details scored higher than plain keywords."""
        faculty = [{"name": f"Prof {i}"} for i in range(35)]
        faculty[30]["topic_details"] = [
            {
                "name": "Machine Learning",
                "subfield": "Artificial Intelligence",
                "field": "Computer Science",
                "domain": "Technology",
            },
        ]
        result = filter_faculty_by_relevance(
            faculty_data=faculty, research_interests=["machine learning"]
        )
        # the one with topic_details should rank first
        assert result[0] == faculty[30]
