"""Unit tests for _session_has_orchestrator function."""
import pytest
from unittest.mock import patch


class TestSessionHasOrchestrator:
    """Tests for _session_has_orchestrator function."""

    @patch('app._load_orchestrators')
    def test_returns_true_when_session_has_enabled_orchestrator(self, mock_load):
        """Test that function returns True when session has enabled orchestrator."""
        from app import _session_has_orchestrator

        mock_load.return_value = {
            "orch-1": {
                "id": "orch-1",
                "name": "Test Orch",
                "enabled": True,
                "managed_sessions": ["test-session", "other-session"]
            }
        }

        result = _session_has_orchestrator("test-session")
        assert result is True

    @patch('app._load_orchestrators')
    def test_returns_false_when_session_has_no_orchestrator(self, mock_load):
        """Test that function returns False when session has no orchestrator."""
        from app import _session_has_orchestrator

        mock_load.return_value = {
            "orch-1": {
                "id": "orch-1",
                "name": "Test Orch",
                "enabled": True,
                "managed_sessions": ["other-session"]
            }
        }

        result = _session_has_orchestrator("test-session")
        assert result is False

    @patch('app._load_orchestrators')
    def test_returns_false_when_orchestrator_disabled(self, mock_load):
        """Test that function returns False when orchestrator is disabled."""
        from app import _session_has_orchestrator

        mock_load.return_value = {
            "orch-1": {
                "id": "orch-1",
                "name": "Test Orch",
                "enabled": False,  # Disabled
                "managed_sessions": ["test-session"]
            }
        }

        result = _session_has_orchestrator("test-session")
        assert result is False

    @patch('app._load_orchestrators')
    def test_returns_false_when_no_orchestrators_exist(self, mock_load):
        """Test that function returns False when no orchestrators exist."""
        from app import _session_has_orchestrator

        mock_load.return_value = {}

        result = _session_has_orchestrator("test-session")
        assert result is False

    @patch('app._load_orchestrators')
    def test_handles_empty_session_name(self, mock_load):
        """Test that function handles empty session name gracefully."""
        from app import _session_has_orchestrator

        result = _session_has_orchestrator("")
        assert result is False
        mock_load.assert_not_called()

    @patch('app._load_orchestrators')
    def test_handles_none_session_name(self, mock_load):
        """Test that function handles None session name gracefully."""
        from app import _session_has_orchestrator

        result = _session_has_orchestrator(None)
        assert result is False
        mock_load.assert_not_called()

    @patch('app._load_orchestrators')
    def test_handles_orchestrator_load_error(self, mock_load):
        """Test that function handles orchestrator load errors gracefully."""
        from app import _session_has_orchestrator

        mock_load.side_effect = Exception("Failed to load")

        result = _session_has_orchestrator("test-session")
        assert result is False

    @patch('app._load_orchestrators')
    def test_checks_multiple_orchestrators(self, mock_load):
        """Test that function correctly checks across multiple orchestrators."""
        from app import _session_has_orchestrator

        mock_load.return_value = {
            "orch-1": {
                "id": "orch-1",
                "enabled": True,
                "managed_sessions": ["session-1"]
            },
            "orch-2": {
                "id": "orch-2",
                "enabled": True,
                "managed_sessions": ["session-2", "test-session"]
            },
            "orch-3": {
                "id": "orch-3",
                "enabled": True,
                "managed_sessions": ["session-3"]
            }
        }

        result = _session_has_orchestrator("test-session")
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
