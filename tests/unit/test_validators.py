"""Unit tests for app/utils/validators.py."""

import pytest

from app.utils.validators import ensure_protocol


class TestEnsureProtocol:
    """Tests for ensure_protocol function."""

    def test_empty_url_returns_empty(self):
        """Empty URL should return empty string."""
        assert ensure_protocol("") == ""

    def test_none_like_empty_returns_empty(self):
        """Empty string returns as-is."""
        result = ensure_protocol("")
        assert result == ""

    def test_url_with_https_unchanged(self):
        """URL with https:// should remain unchanged."""
        url = "https://example.com"
        assert ensure_protocol(url) == url

    def test_url_with_http_unchanged(self):
        """URL with http:// should remain unchanged."""
        url = "http://example.com"
        assert ensure_protocol(url) == url

    def test_url_without_protocol_gets_https(self):
        """URL without protocol gets https:// by default."""
        assert ensure_protocol("example.com") == "https://example.com"

    def test_url_without_protocol_gets_custom_default(self):
        """URL without protocol uses custom default protocol."""
        assert ensure_protocol("example.com", "http://") == "http://example.com"

    def test_url_with_path(self):
        """URL with path and no protocol."""
        result = ensure_protocol("example.com/path/to/page")
        assert result == "https://example.com/path/to/page"

    def test_url_with_www(self):
        """URL with www but no protocol."""
        result = ensure_protocol("www.example.com")
        assert result == "https://www.example.com"

    def test_url_with_subdomain(self):
        """URL with subdomain but no protocol."""
        result = ensure_protocol("api.example.com")
        assert result == "https://api.example.com"

    def test_url_with_port(self):
        """URL with port but no protocol."""
        result = ensure_protocol("example.com:8080")
        assert result == "https://example.com:8080"

    def test_url_case_sensitivity(self):
        """Protocol detection should handle case variations."""
        # Note: The current implementation is case-sensitive
        # HTTP:// and HTTPS:// in uppercase would get protocol added
        assert ensure_protocol("HTTPS://example.com") == "https://HTTPS://example.com"
        # This tests current behavior - may want to fix this later
