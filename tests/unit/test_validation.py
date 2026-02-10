"""Unit tests for validation functions."""
import pytest
from utils.validation import _validate_name, _validate_provider, _validate_schedule


class TestValidateName:
    """Tests for _validate_name function."""

    def test_valid_name_passes(self):
        """Should accept valid session names."""
        assert _validate_name("my-session", "session") is None
        assert _validate_name("test_123", "session") is None
        assert _validate_name("valid-name", "session") is None

    def test_empty_name_fails(self):
        """Should reject empty names."""
        error = _validate_name("", "session")
        assert error is not None
        assert "required" in error.lower()

    def test_none_name_fails(self):
        """Should reject None names."""
        error = _validate_name(None, "session")
        assert error is not None

    def test_whitespace_only_fails(self):
        """Should reject whitespace-only names."""
        error = _validate_name("   ", "session")
        assert error is not None


class TestValidateProvider:
    """Tests for _validate_provider function."""

    def test_valid_providers_pass(self):
        """Should accept valid provider names."""
        assert _validate_provider("codex") is None
        assert _validate_provider("claude") is None
        assert _validate_provider("copilot") is None
        assert _validate_provider("gemini") is None

    def test_invalid_provider_fails(self):
        """Should reject invalid provider names."""
        error = _validate_provider("chatgpt")
        assert error is not None
        assert "provider" in error.lower()

    def test_empty_provider_fails(self):
        """Should reject empty provider."""
        error = _validate_provider("")
        assert error is not None

    def test_case_insensitive(self):
        """Should accept providers in any case."""
        assert _validate_provider("CODEX") is None
        assert _validate_provider("Claude") is None


class TestValidateSchedule:
    """Tests for _validate_schedule function."""

    def test_none_schedule_passes(self):
        """Should accept None schedule."""
        assert _validate_schedule(None) is None

    def test_manual_schedule_passes(self):
        """Should accept manual schedule."""
        assert _validate_schedule({"type": "manual"}) is None

    def test_interval_schedule_passes(self):
        """Should accept valid interval schedule."""
        assert _validate_schedule({
            "type": "interval",
            "minutes": 30
        }) is None

    def test_daily_schedule_passes(self):
        """Should accept valid daily schedule."""
        assert _validate_schedule({
            "type": "daily",
            "time": "09:00"
        }) is None

    def test_weekly_schedule_passes(self):
        """Should accept valid weekly schedule."""
        assert _validate_schedule({
            "type": "weekly",
            "days": ["mon", "wed", "fri"],
            "time": "09:00"
        }) is None

    def test_monthly_schedule_passes(self):
        """Should accept valid monthly schedule."""
        assert _validate_schedule({
            "type": "monthly",
            "day_of_month": 15,
            "time": "09:00"
        }) is None

    def test_invalid_type_fails(self):
        """Should reject invalid schedule type."""
        error = _validate_schedule({"type": "yearly"})
        assert error is not None
        assert "type" in error.lower()

    def test_non_dict_schedule_fails(self):
        """Should reject non-dict schedules."""
        error = _validate_schedule("invalid")
        assert error is not None
