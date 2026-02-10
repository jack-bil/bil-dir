"""Unit tests for helper functions."""
import pytest
import os
from app import _safe_cwd, _error_response, _format_duration


class TestSafeCwd:
    """Tests for _safe_cwd function."""

    def test_returns_absolute_path_for_valid_input(self):
        """Should convert relative paths to absolute."""
        result = _safe_cwd(".")
        assert os.path.isabs(result)

    def test_handles_none_gracefully(self):
        """Should return default path for None."""
        result = _safe_cwd(None)
        assert os.path.isabs(result)

    def test_handles_empty_string(self):
        """Should return default path for empty string."""
        result = _safe_cwd("")
        assert os.path.isabs(result)

    def test_preserves_absolute_paths(self):
        """Should preserve already absolute paths."""
        test_path = "C:\\Users\\test"
        result = _safe_cwd(test_path)
        assert result == os.path.abspath(test_path)


class TestErrorResponse:
    """Tests for _error_response function."""

    def test_basic_error_response(self, app):
        """Should return JSON response with error message."""
        with app.app_context():
            response, status = _error_response("Test error")

            assert status == 400
            data = response.get_json()
            assert data["error"] == "Test error"

    def test_error_with_code(self, app):
        """Should include error code when provided."""
        with app.app_context():
            response, status = _error_response("Test error", code="TEST_ERROR")

            data = response.get_json()
            assert data["error"] == "Test error"
            assert data["code"] == "TEST_ERROR"

    def test_error_with_details(self, app):
        """Should include details when provided."""
        with app.app_context():
            details = {"field": "name", "constraint": "required"}
            response, status = _error_response("Test error", details=details)

            data = response.get_json()
            assert data["error"] == "Test error"
            assert data["details"] == details

    def test_custom_status_code(self, app):
        """Should allow custom status codes."""
        with app.app_context():
            response, status = _error_response("Not found", status=404)

            assert status == 404

    def test_all_fields_together(self, app):
        """Should handle all fields at once."""
        with app.app_context():
            response, status = _error_response(
                "Validation failed",
                code="VALIDATION_ERROR",
                details={"fields": ["name", "email"]},
                status=422
            )

            assert status == 422
            data = response.get_json()
            assert data["error"] == "Validation failed"
            assert data["code"] == "VALIDATION_ERROR"
            assert "fields" in data["details"]


class TestFormatDuration:
    """Tests for _format_duration function."""

    def test_formats_seconds(self):
        """Should format seconds only."""
        assert _format_duration(30) == "30s"

    def test_formats_minutes_and_seconds(self):
        """Should format minutes and seconds."""
        assert _format_duration(90) == "1m 30s"

    def test_formats_hours(self):
        """Should format hours, minutes, and seconds."""
        assert _format_duration(3661) == "1h 1m 1s"

    def test_formats_days(self):
        """Should format days, hours, minutes, and seconds."""
        assert _format_duration(86400 + 3600 + 60 + 1) == "1d 1h 1m 1s"

    def test_handles_zero(self):
        """Should handle zero duration."""
        assert _format_duration(0) == "0s"

    def test_handles_negative_as_zero(self):
        """Should treat negative as zero."""
        assert _format_duration(-10) == "0s"

    def test_omits_zero_components(self):
        """Should not show zero components."""
        result = _format_duration(61)  # 1 minute 1 second, 0 hours
        assert "1m 1s" == result
        assert "0h" not in result
