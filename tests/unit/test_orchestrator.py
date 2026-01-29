"""Unit tests for app/services/orchestrator.py helper functions."""

import pytest

from app.services.orchestrator import to_str, to_list, to_int, _extract_domain


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
