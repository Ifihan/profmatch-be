"""Unit tests for app/utils/storage.py."""

import pytest
from pathlib import Path

from app.utils.storage import validate_extension, ALLOWED_EXTENSIONS


class TestValidateExtension:
    """Tests for validate_extension function."""

    def test_pdf_allowed(self):
        """PDF files should be allowed."""
        assert validate_extension("document.pdf") is True

    def test_docx_allowed(self):
        """DOCX files should be allowed."""
        assert validate_extension("resume.docx") is True

    def test_txt_allowed(self):
        """TXT files should be allowed."""
        assert validate_extension("notes.txt") is True

    def test_uppercase_extensions_allowed(self):
        """Uppercase extensions should be allowed."""
        assert validate_extension("document.PDF") is True
        assert validate_extension("resume.DOCX") is True
        assert validate_extension("notes.TXT") is True

    def test_mixed_case_extensions_allowed(self):
        """Mixed case extensions should be allowed."""
        assert validate_extension("document.Pdf") is True
        assert validate_extension("resume.DocX") is True

    def test_exe_not_allowed(self):
        """EXE files should not be allowed."""
        assert validate_extension("virus.exe") is False

    def test_py_not_allowed(self):
        """Python files should not be allowed."""
        assert validate_extension("script.py") is False

    def test_jpg_not_allowed(self):
        """Image files should not be allowed."""
        assert validate_extension("photo.jpg") is False
        assert validate_extension("photo.png") is False

    def test_no_extension_not_allowed(self):
        """Files without extension should not be allowed."""
        assert validate_extension("noextension") is False

    def test_hidden_file_not_allowed(self):
        """Hidden files (starting with .) should not be allowed."""
        assert validate_extension(".hidden") is False

    def test_double_extension_uses_last(self):
        """Double extensions should use the last one."""
        assert validate_extension("document.exe.pdf") is True
        assert validate_extension("resume.pdf.exe") is False

    def test_empty_filename(self):
        """Empty filename should not be allowed."""
        assert validate_extension("") is False

    def test_path_with_filename(self):
        """Full path with valid filename should work."""
        assert validate_extension("/path/to/document.pdf") is True
        assert validate_extension("C:\\Users\\docs\\resume.docx") is True

    def test_filename_with_spaces(self):
        """Filenames with spaces should work."""
        assert validate_extension("my resume.pdf") is True
        assert validate_extension("my document.docx") is True

    def test_allowed_extensions_constant(self):
        """ALLOWED_EXTENSIONS should contain expected values."""
        assert ALLOWED_EXTENSIONS == {".pdf", ".docx", ".txt"}
