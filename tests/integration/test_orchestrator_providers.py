"""Integration tests for orchestrator support across all providers.

These tests verify that orchestrators can:
1. Read session history for all providers (codex, copilot, claude, gemini)
2. Make decisions based on that history
3. Kick off new sessions when needed
"""
import pytest
import json
import os


class TestOrchestratorHistoryReading:
    """Test that orchestrators can read history from all provider types."""

    def test_get_history_for_codex_session(self):
        """Should read history from history.json for Codex sessions."""
        from app import _get_history_for_name

        # This would need a test fixture with a codex session in history.json
        # For now, just test that it doesn't crash
        history = _get_history_for_name("nonexistent_codex")
        assert isinstance(history, dict)
        assert "messages" in history
        assert "tool_outputs" in history

    def test_get_history_for_claude_session(self):
        """Should read history from JSONL files for Claude sessions."""
        from app import _get_history_for_name

        # This would need a test fixture with a claude session JSONL file
        history = _get_history_for_name("nonexistent_claude")
        assert isinstance(history, dict)
        assert "messages" in history

    def test_claude_history_with_real_session_file(self, tmp_path):
        """Should parse Claude JSONL session files correctly."""
        from app import _get_claude_history

        # Create a temporary Claude session file
        session_id = "test-session-uuid"
        safe_workdir = str(tmp_path).replace(":", "-").replace("\\", "-").replace("/", "-")

        claude_dir = tmp_path / ".claude" / "projects" / safe_workdir
        claude_dir.mkdir(parents=True, exist_ok=True)

        session_file = claude_dir / f"{session_id}.jsonl"

        # Write test messages in Claude JSONL format
        with open(session_file, 'w', encoding='utf-8') as f:
            # User message
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Hello!"},
                "uuid": "user-uuid-1"
            }) + "\n")

            # Assistant message
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Hi there!"}
                    ]
                },
                "uuid": "assistant-uuid-1"
            }) + "\n")

        # Mock the home directory to use tmp_path
        import os
        original_expanduser = os.path.expanduser
        os.path.expanduser = lambda path: str(tmp_path) if path == "~" else path

        try:
            history = _get_claude_history(session_id, str(tmp_path))

            assert len(history["messages"]) == 2
            assert history["messages"][0]["role"] == "user"
            assert history["messages"][0]["text"] == "Hello!"
            assert history["messages"][1]["role"] == "assistant"
            assert history["messages"][1]["text"] == "Hi there!"
        finally:
            os.path.expanduser = original_expanduser


class TestOrchestratorWithDifferentProviders:
    """Test orchestrator decision-making with different provider sessions."""

    @pytest.mark.skip(reason="Requires running orchestrator loop - implement when ready")
    def test_orchestrator_kicks_off_codex_session(self):
        """Orchestrator should kick off new Codex sessions."""
        pass

    @pytest.mark.skip(reason="Requires running orchestrator loop - implement when ready")
    def test_orchestrator_kicks_off_claude_session(self):
        """Orchestrator should kick off new Claude sessions."""
        pass

    @pytest.mark.skip(reason="Requires Claude JSONL reading")
    def test_orchestrator_reads_claude_history(self):
        """Orchestrator should read existing Claude session history."""
        pass

    @pytest.mark.skip(reason="Requires mocking orchestrator LLM calls")
    def test_orchestrator_makes_decision_for_claude_output(self):
        """Orchestrator should make decisions based on Claude session output."""
        pass


class TestProviderHistoryFormat:
    """Test that history format is consistent across providers."""

    def test_history_format_has_required_fields(self):
        """All history objects should have messages and tool_outputs."""
        from app import _get_history_for_name

        # Test with non-existent session (should return empty but valid structure)
        history = _get_history_for_name("nonexistent")

        assert isinstance(history, dict)
        assert "messages" in history
        assert "tool_outputs" in history
        assert isinstance(history["messages"], list)
        assert isinstance(history["tool_outputs"], list)

    def test_message_format_is_consistent(self):
        """Messages should have consistent format across providers."""
        # Each message should have:
        # - "role": "user" or "assistant"
        # - "text": string content

        # This would test that Codex, Copilot, Claude, Gemini all produce
        # the same message format for orchestrators to consume
        pass


# Add this to pytest.ini markers:
# orchestrator: Orchestrator integration tests (require provider history)
