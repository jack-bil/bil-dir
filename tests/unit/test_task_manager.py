"""Unit tests for task manager functions."""
import pytest
from core.task_manager import _normalize_task, _build_task_history_text


class TestNormalizeTask:
    """Tests for _normalize_task function."""

    def test_basic_task_normalization(self):
        """Should normalize basic task with defaults."""
        task = _normalize_task({
            "id": "test123",
            "name": "Test",
            "prompt": "Do thing"
        })

        assert task["id"] == "test123"
        assert task["name"] == "Test"
        assert task["prompt"] == "Do thing"
        assert task["provider"] == "codex"  # default
        assert task["enabled"] is True  # default
        assert isinstance(task["schedule"], dict)

    def test_invalid_provider_defaults_to_codex(self):
        """Should use codex for unknown providers."""
        task = _normalize_task({
            "name": "Test",
            "prompt": "Do thing",
            "provider": "chatgpt"  # invalid
        })

        assert task["provider"] == "codex"

    def test_preserves_valid_schedule(self):
        """Should preserve valid schedule types."""
        task = _normalize_task({
            "name": "Test",
            "prompt": "Do thing",
            "schedule": {"type": "daily", "time": "09:00"}
        })

        assert task["schedule"]["type"] == "daily"
        assert task["schedule"]["time"] == "09:00"

    def test_invalid_schedule_defaults_to_manual(self):
        """Should default to manual for invalid schedules."""
        task = _normalize_task({
            "name": "Test",
            "prompt": "Do thing",
            "schedule": "invalid"
        })

        assert task["schedule"]["type"] == "manual"

    def test_preserves_workdir(self):
        """Should preserve working directory."""
        task = _normalize_task({
            "name": "Test",
            "prompt": "Do thing",
            "workdir": "/some/path"
        })

        assert task["workdir"] == "/some/path"

    def test_initializes_run_history_as_list(self):
        """Should initialize run_history as empty list."""
        task = _normalize_task({
            "name": "Test",
            "prompt": "Do thing"
        })

        assert isinstance(task["run_history"], list)
        assert len(task["run_history"]) == 0


class TestBuildTaskHistoryText:
    """Tests for _build_task_history_text function."""

    def test_empty_history_returns_empty_string(self):
        """Should return empty string for no history."""
        result = _build_task_history_text(None, "output")
        assert result == ""

        result = _build_task_history_text([], "output")
        assert result == ""

    def test_extracts_output_field(self):
        """Should extract specified field from history."""
        history = [
            {"started_at": "2026-01-01", "output": "Result 1"},
            {"started_at": "2026-01-02", "output": "Result 2"}
        ]

        result = _build_task_history_text(history, "output")
        assert "Result 1" in result
        assert "Result 2" in result

    def test_extracts_raw_output_field(self):
        """Should extract raw_output when specified."""
        history = [
            {"started_at": "2026-01-01", "raw_output": "Raw 1", "output": "Clean 1"}
        ]

        result = _build_task_history_text(history, "raw_output")
        assert "Raw 1" in result
        assert "Clean 1" not in result

    def test_includes_timestamps(self):
        """Should include timestamp information or runtime."""
        history = [
            {"started_at": "2026-01-01T10:00:00", "output": "Test", "runtime_sec": 1.5}
        ]

        result = _build_task_history_text(history, "output")
        # Should include either timestamp or runtime info
        assert ("2026-01-01" in result or "runtime" in result)

    def test_handles_missing_fields_gracefully(self):
        """Should handle missing fields without crashing."""
        history = [
            {"started_at": "2026-01-01"},  # no output
            {"output": "Test"}  # no timestamp
        ]

        # Should not raise exception
        result = _build_task_history_text(history, "output")
        assert isinstance(result, str)
