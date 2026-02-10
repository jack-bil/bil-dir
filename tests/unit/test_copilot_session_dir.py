"""Unit test to verify Copilot session directory is created on startup."""
import pytest
from pathlib import Path


class TestCopilotSessionDirectory:
    """Test that Copilot session-state directory exists."""

    def test_copilot_session_dir_exists(self):
        """Test that .copilot/session-state directory is created."""
        copilot_session_dir = Path.home() / ".copilot" / "session-state"

        assert copilot_session_dir.exists(), \
            f"Copilot session-state directory missing: {copilot_session_dir}"
        assert copilot_session_dir.is_dir(), \
            f"Copilot session-state path is not a directory: {copilot_session_dir}"

    def test_copilot_session_dir_writable(self):
        """Test that session-state directory is writable."""
        copilot_session_dir = Path.home() / ".copilot" / "session-state"

        # Try to create a test file
        test_file = copilot_session_dir / "test_write.txt"
        try:
            test_file.write_text("test")
            assert test_file.exists()
            test_file.unlink()  # Clean up
        except Exception as e:
            pytest.fail(f"Cannot write to session-state directory: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
