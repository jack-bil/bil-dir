"""Integration tests for session persistence across multiple requests.

These tests verify that:
1. Messages are saved to history
2. Sessions are resumed properly (session ID doesn't change)
3. History accumulates correctly across multiple exchanges
"""
import pytest
import requests
import time
import json


class TestCopilotSessionPersistence:
    """Test that Copilot sessions persist properly."""

    BASE_URL = "http://localhost:5025"

    def test_copilot_session_persists_across_messages(self):
        """Test that Copilot sessions maintain the same ID and accumulate history."""
        session_name = f"test_persist_{int(time.time())}"

        # First message
        response1 = requests.post(f"{self.BASE_URL}/stream", json={
            "prompt": "Say 'hello from message 1'",
            "session_name": session_name,
            "provider": "copilot",
            "timeout_sec": 60
        }, stream=True, timeout=70)

        assert response1.status_code == 200

        # Extract session ID from SSE stream
        session_id_1 = None
        for line in response1.iter_lines(decode_unicode=True):
            if line.startswith("data:") and "session_id" in line:
                try:
                    session_id_1 = line.split("data:")[1].strip()
                except:
                    pass

        # Wait for job to complete
        time.sleep(5)

        # Check history after first message
        history_response = requests.get(f"{self.BASE_URL}/api/sessions/{session_name}/history")
        assert history_response.status_code == 200
        history_1 = history_response.json()
        messages_1 = history_1.get("messages", [])

        # Should have at least user message
        assert len(messages_1) >= 1, f"Expected at least 1 message, got {len(messages_1)}"
        assert any(m.get("role") == "user" and "message 1" in m.get("text", "").lower()
                   for m in messages_1), "First message not found in history"

        # Second message to same session
        response2 = requests.post(f"{self.BASE_URL}/stream", json={
            "prompt": "Say 'hello from message 2'",
            "session_name": session_name,
            "provider": "copilot",
            "timeout_sec": 60
        }, stream=True, timeout=70)

        assert response2.status_code == 200

        # Extract session ID from second request
        session_id_2 = None
        for line in response2.iter_lines(decode_unicode=True):
            if line.startswith("data:") and "session_id" in line:
                try:
                    session_id_2 = line.split("data:")[1].strip()
                except:
                    pass

        # Wait for second job to complete
        time.sleep(5)

        # Check history after second message
        history_response2 = requests.get(f"{self.BASE_URL}/api/sessions/{session_name}/history")
        assert history_response2.status_code == 200
        history_2 = history_response2.json()
        messages_2 = history_2.get("messages", [])

        # CRITICAL: Session ID should NOT change
        if session_id_1 and session_id_2:
            assert session_id_1 == session_id_2, \
                f"Session ID changed! First: {session_id_1}, Second: {session_id_2}"

        # Should have messages from BOTH exchanges
        assert len(messages_2) >= len(messages_1) + 1, \
            f"History not accumulating. First had {len(messages_1)}, now has {len(messages_2)}"

        # Verify both messages are present
        has_msg_1 = any(m.get("role") == "user" and "message 1" in m.get("text", "").lower()
                        for m in messages_2)
        has_msg_2 = any(m.get("role") == "user" and "message 2" in m.get("text", "").lower()
                        for m in messages_2)

        assert has_msg_1, "First message lost from history!"
        assert has_msg_2, "Second message not in history!"

        # Cleanup
        requests.delete(f"{self.BASE_URL}/sessions/{session_name}")

    def test_codex_session_persists_across_messages(self):
        """Test that Codex sessions maintain history."""
        session_name = f"test_codex_persist_{int(time.time())}"

        # First message
        response1 = requests.post(f"{self.BASE_URL}/stream", json={
            "prompt": "echo 'test message 1'",
            "session_name": session_name,
            "provider": "codex",
            "timeout_sec": 30
        }, stream=True, timeout=40)

        assert response1.status_code == 200
        time.sleep(2)

        # Check history
        history_response = requests.get(f"{self.BASE_URL}/api/sessions/{session_name}/history")
        assert history_response.status_code == 200
        history_1 = history_response.json()
        messages_1 = history_1.get("messages", [])
        initial_count = len(messages_1)

        # Second message
        response2 = requests.post(f"{self.BASE_URL}/stream", json={
            "prompt": "echo 'test message 2'",
            "session_name": session_name,
            "provider": "codex",
            "timeout_sec": 30
        }, stream=True, timeout=40)

        assert response2.status_code == 200
        time.sleep(2)

        # Verify history accumulated
        history_response2 = requests.get(f"{self.BASE_URL}/api/sessions/{session_name}/history")
        assert history_response2.status_code == 200
        history_2 = history_response2.json()
        messages_2 = history_2.get("messages", [])

        assert len(messages_2) > initial_count, \
            f"Codex history not accumulating. Was {initial_count}, now {len(messages_2)}"

        # Cleanup
        requests.delete(f"{self.BASE_URL}/sessions/{session_name}")


class TestHistoryPersistence:
    """Test that history is properly saved and loaded."""

    BASE_URL = "http://localhost:5025"

    def test_history_persists_after_server_restart(self):
        """Test that history survives across server restarts (simulated by checking file)."""
        session_name = f"test_history_persist_{int(time.time())}"

        # Send a message
        response = requests.post(f"{self.BASE_URL}/stream", json={
            "prompt": "echo 'persistent message'",
            "session_name": session_name,
            "provider": "codex",
            "timeout_sec": 30
        }, stream=True, timeout=40)

        assert response.status_code == 200
        time.sleep(2)

        # Get history from API
        api_history = requests.get(f"{self.BASE_URL}/api/sessions/{session_name}/history").json()
        api_messages = api_history.get("messages", [])

        # Read history.json file directly
        import json
        import pathlib
        history_file = pathlib.Path("history.json")

        if history_file.exists():
            with open(history_file, 'r') as f:
                file_history = json.load(f)

            # Find this session's history in the file
            session_found = False
            for session_id, session_data in file_history.items():
                if session_data.get("session_name") == session_name:
                    session_found = True
                    file_messages = session_data.get("messages", [])

                    # API and file should match
                    assert len(file_messages) == len(api_messages), \
                        f"History file mismatch: file has {len(file_messages)}, API has {len(api_messages)}"
                    break

            assert session_found, "Session not found in history.json file!"

        # Cleanup
        requests.delete(f"{self.BASE_URL}/sessions/{session_name}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
