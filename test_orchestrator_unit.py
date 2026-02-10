"""Unit tests for orchestrator functions.

These tests run in isolation without requiring the Flask app to be running.
They use mocks to simulate dependencies.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import queue


# Import the functions we want to test
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


class TestTriggerOrchestratorCheck:
    """Tests for _trigger_orchestrator_check function."""

    @patch('app._ORCH_TRIGGER_QUEUE')
    def test_trigger_adds_session_to_queue(self, mock_queue):
        """Test that triggering adds the session name to the queue."""
        from app import _trigger_orchestrator_check

        session_name = "test-session"
        _trigger_orchestrator_check(session_name)

        mock_queue.put_nowait.assert_called_once_with(session_name)

    @patch('app._ORCH_TRIGGER_QUEUE')
    def test_trigger_ignores_empty_session_name(self, mock_queue):
        """Test that empty session name is ignored."""
        from app import _trigger_orchestrator_check

        _trigger_orchestrator_check("")
        mock_queue.put_nowait.assert_not_called()

        _trigger_orchestrator_check(None)
        mock_queue.put_nowait.assert_not_called()

    @patch('app._ORCH_TRIGGER_QUEUE')
    def test_trigger_handles_full_queue_gracefully(self, mock_queue):
        """Test that full queue doesn't crash."""
        from app import _trigger_orchestrator_check

        mock_queue.put_nowait.side_effect = queue.Full()

        # Should not raise exception
        _trigger_orchestrator_check("test-session")


class TestFormatRecentHistory:
    """Tests for _format_recent_history function."""

    @patch('app._get_history_for_name')
    def test_format_with_string_text(self, mock_get_history):
        """Test formatting history where text is a string."""
        from app import _format_recent_history

        mock_get_history.return_value = {
            "messages": [
                {"role": "user", "text": "Hello"},
                {"role": "assistant", "text": "Hi there"}
            ]
        }

        result = _format_recent_history("test-session")

        assert "[User] Hello" in result
        assert "[Assistant] Hi there" in result

    @patch('app._get_history_for_name')
    def test_format_with_list_text(self, mock_get_history):
        """Test formatting history where text is a list (bug fix)."""
        from app import _format_recent_history

        mock_get_history.return_value = {
            "messages": [
                {"role": "assistant", "text": ["Line 1", "Line 2"]}
            ]
        }

        # Should not crash and should handle list
        result = _format_recent_history("test-session")

        assert "[Assistant]" in result
        assert "Line 1" in result
        assert "Line 2" in result

    @patch('app._get_history_for_name')
    def test_format_empty_history(self, mock_get_history):
        """Test formatting empty history."""
        from app import _format_recent_history

        mock_get_history.return_value = {"messages": []}

        result = _format_recent_history("test-session")

        assert result == ""

    @patch('app._get_history_for_name')
    def test_format_respects_limit(self, mock_get_history):
        """Test that limit parameter works."""
        from app import _format_recent_history

        mock_get_history.return_value = {
            "messages": [
                {"role": "user", "text": f"Message {i}"}
                for i in range(10)
            ]
        }

        result = _format_recent_history("test-session", limit=3)

        # Should only show last 3 messages
        assert "Message 9" in result
        assert "Message 8" in result
        assert "Message 7" in result
        assert "Message 0" not in result

    @patch('app._get_history_for_name')
    def test_format_handles_orchestrator_role(self, mock_get_history):
        """Test that system role is labeled as Orchestrator."""
        from app import _format_recent_history

        mock_get_history.return_value = {
            "messages": [
                {"role": "system", "text": "Do this task"}
            ]
        }

        result = _format_recent_history("test-session")

        assert "[Orchestrator] Do this task" in result


class TestGetLatestAssistantMessage:
    """Tests for _get_latest_assistant_message_with_index function."""

    @patch('app._get_history_for_name')
    def test_get_latest_with_string_text(self, mock_get_history):
        """Test getting latest message where text is a string."""
        from app import _get_latest_assistant_message_with_index

        mock_get_history.return_value = {
            "messages": [
                {"role": "user", "text": "Hello"},
                {"role": "assistant", "text": "Response"}
            ]
        }

        idx, text = _get_latest_assistant_message_with_index("test-session")

        assert idx == 1
        assert text == "Response"

    @patch('app._get_history_for_name')
    def test_get_latest_with_list_text(self, mock_get_history):
        """Test getting latest message where text is a list (bug fix)."""
        from app import _get_latest_assistant_message_with_index

        mock_get_history.return_value = {
            "messages": [
                {"role": "assistant", "text": ["Part 1", "Part 2"]}
            ]
        }

        # Should not crash and should handle list
        idx, text = _get_latest_assistant_message_with_index("test-session")

        assert idx == 0
        assert "Part 1" in text
        assert "Part 2" in text

    @patch('app._get_history_for_name')
    def test_get_latest_returns_error_messages(self, mock_get_history):
        """Test that error messages are also returned."""
        from app import _get_latest_assistant_message_with_index

        mock_get_history.return_value = {
            "messages": [
                {"role": "error", "text": "Command failed"}
            ]
        }

        idx, text = _get_latest_assistant_message_with_index("test-session")

        assert idx == 0
        assert text == "Command failed"

    @patch('app._get_history_for_name')
    def test_get_latest_skips_user_messages(self, mock_get_history):
        """Test that only assistant/error messages are returned."""
        from app import _get_latest_assistant_message_with_index

        mock_get_history.return_value = {
            "messages": [
                {"role": "assistant", "text": "First"},
                {"role": "user", "text": "Second"}
            ]
        }

        # Should return the assistant message, not the user message
        idx, text = _get_latest_assistant_message_with_index("test-session")

        assert idx == 0
        assert text == "First"

    @patch('app._get_history_for_name')
    def test_get_latest_empty_history(self, mock_get_history):
        """Test handling of empty history."""
        from app import _get_latest_assistant_message_with_index

        mock_get_history.return_value = {"messages": []}

        idx, text = _get_latest_assistant_message_with_index("test-session")

        assert idx == -1
        assert text == ""


class TestProcessOrchestratorSession:
    """Tests for orchestrator session processing logic."""

    @patch('app._get_session_status')
    def test_skips_running_sessions(self, mock_status):
        """Test that running sessions are not processed."""
        from app import _process_orchestrator_session

        mock_status.return_value = "running"

        orch = {"id": "test-orch", "enabled": True}
        state = {}

        result = _process_orchestrator_session("test-orch", orch, "test-session", state)

        # Should update status but not handle
        assert result["status"] == "running"
        assert result["handled_idle"] == False

    @patch('app._get_session_status')
    @patch('app._get_history_for_name')
    @patch('app._inject_prompt_to_session')
    def test_kickoff_sent_for_new_idle_session(self, mock_inject, mock_history, mock_status):
        """Test that kickoff is sent for new idle session."""
        from app import _process_orchestrator_session

        mock_status.return_value = "idle"
        mock_history.return_value = {"messages": []}

        orch = {
            "id": "test-orch",
            "enabled": True,
            "goal": "Test goal",
            "history": []
        }
        state = {}

        with patch('app._get_orchestrator_worker_prompt') as mock_worker_prompt:
            with patch('app._build_worker_kickoff_prompt') as mock_build_kickoff:
                with patch('app._append_orchestrator_history'):
                    with patch('app._infer_worker_role', return_value="developer"):
                        with patch('app._get_session_workdir', return_value="/test"):
                            with patch('app._load_client_config', return_value={}):
                                mock_worker_prompt.return_value = "{goal}"
                                mock_build_kickoff.return_value = "Test goal"

                                result = _process_orchestrator_session("test-orch", orch, "test-session", state)

        # Should have sent kickoff
        assert result["kickoff_sent"] == True
        assert result["handled_idle"] == True
        mock_inject.assert_called_once()


if __name__ == "__main__":
    # Run with: python -m pytest test_orchestrator_unit.py -v
    pytest.main([__file__, "-v", "--tb=short"])
