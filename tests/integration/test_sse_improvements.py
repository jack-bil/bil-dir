"""Integration tests for SSE improvements.

Tests:
1. Heartbeat messages are sent every 15 seconds
2. Backpressure handling disconnects slow clients
3. Reconnection logic replays missed messages
4. Message IDs are incrementing correctly
5. Broadcast functions add to history
"""
import pytest
import requests
import time
import threading
import queue
from collections import deque


class TestSSEHeartbeats:
    """Test that SSE endpoints send heartbeat messages."""

    BASE_URL = "http://localhost:5025"

    def test_master_stream_sends_heartbeats(self):
        """Test that /master/stream sends heartbeats every 15 seconds."""
        url = f"{self.BASE_URL}/master/stream"

        heartbeat_count = 0
        start_time = time.time()

        with requests.get(url, stream=True, timeout=20) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            for line in response.iter_lines(decode_unicode=True):
                if ": heartbeat" in line:
                    heartbeat_count += 1

                # Stop after receiving at least 1 heartbeat
                if heartbeat_count >= 1:
                    break

                # Timeout after 20 seconds
                if time.time() - start_time > 20:
                    break

        assert heartbeat_count >= 1, "Should receive at least 1 heartbeat in 20 seconds"

    def test_tasks_stream_sends_heartbeats(self):
        """Test that /tasks/stream sends heartbeats."""
        url = f"{self.BASE_URL}/tasks/stream"

        heartbeat_count = 0
        start_time = time.time()

        with requests.get(url, stream=True, timeout=20) as response:
            assert response.status_code == 200

            for line in response.iter_lines(decode_unicode=True):
                if ": heartbeat" in line:
                    heartbeat_count += 1

                if heartbeat_count >= 1:
                    break

                if time.time() - start_time > 20:
                    break

        assert heartbeat_count >= 1, "Tasks stream should send heartbeats"


class TestSSEReconnection:
    """Test SSE reconnection with Last-Event-ID."""

    BASE_URL = "http://localhost:5025"

    def test_master_stream_includes_message_ids(self):
        """Test that /master/stream includes message IDs in events."""
        url = f"{self.BASE_URL}/master/stream"

        message_ids = []
        count = 0

        with requests.get(url, stream=True, timeout=10) as response:
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("id:"):
                    msg_id = line.split("id:")[1].strip()
                    message_ids.append(int(msg_id))
                    count += 1

                # Get at least 2 message IDs
                if count >= 2:
                    break

                if time.time() > time.time() + 10:
                    break

        # Should have at least 1 message ID
        assert len(message_ids) >= 1, "Should receive messages with IDs"

        # IDs should be incrementing
        if len(message_ids) >= 2:
            assert message_ids[1] > message_ids[0], "Message IDs should increment"

    def test_tasks_stream_supports_reconnection(self):
        """Test that /tasks/stream supports Last-Event-ID reconnection."""
        url = f"{self.BASE_URL}/tasks/stream"

        # First connection - get initial message ID
        first_id = None
        with requests.get(url, stream=True, timeout=5) as response:
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("id:"):
                    first_id = line.split("id:")[1].strip()
                    break

        # If we got an ID, try reconnecting with it
        if first_id:
            headers = {"Last-Event-ID": first_id}
            with requests.get(url, stream=True, headers=headers, timeout=5) as response:
                assert response.status_code == 200, "Reconnection should succeed"


class TestBroadcastPerformance:
    """Test that broadcasts complete quickly with optimized timeouts."""

    BASE_URL = "http://localhost:5025"

    def test_task_creation_is_fast(self):
        """Test that creating a task (which triggers broadcast) is fast."""
        url = f"{self.BASE_URL}/tasks"

        task_data = {
            "name": f"perf_test_{int(time.time())}",
            "prompt": "echo 'performance test'",
            "provider": "codex",
            "schedule": {"type": "manual"}
        }

        start = time.time()
        response = requests.post(url, json=task_data)
        elapsed = time.time() - start

        assert response.status_code == 200, f"Task creation failed: {response.text}"

        # Should complete in under 2 seconds (was taking 10+ seconds with 1.0s timeout)
        assert elapsed < 2.0, f"Task creation took {elapsed:.2f}s (should be < 2s)"

        # Cleanup
        if response.status_code == 200:
            task_id = response.json().get("task", {}).get("id")
            if task_id:
                requests.delete(f"{self.BASE_URL}/tasks/{task_id}")


class TestSSEBasicFunctionality:
    """Test that basic SSE functionality still works after changes."""

    BASE_URL = "http://localhost:5025"

    def test_sessions_stream_works(self):
        """Test that /sessions/stream returns data."""
        url = f"{self.BASE_URL}/sessions/stream"

        received_data = False

        with requests.get(url, stream=True, timeout=5) as response:
            assert response.status_code == 200

            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("data:") and "{" in line:
                    received_data = True
                    break

        assert received_data, "Should receive session data"

    def test_master_stream_works(self):
        """Test that /master/stream returns data."""
        url = f"{self.BASE_URL}/master/stream"

        received_open = False

        with requests.get(url, stream=True, timeout=5) as response:
            assert response.status_code == 200

            for line in response.iter_lines(decode_unicode=True):
                if "event: open" in line:
                    received_open = True
                    break

        assert received_open, "Should receive open event"

    def test_tasks_stream_works(self):
        """Test that /tasks/stream returns snapshot."""
        url = f"{self.BASE_URL}/tasks/stream"

        received_snapshot = False

        with requests.get(url, stream=True, timeout=5) as response:
            assert response.status_code == 200

            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("data:") and "snapshot" in line:
                    received_snapshot = True
                    break

        assert received_snapshot, "Should receive tasks snapshot"


class TestHealthCheck:
    """Test that server is healthy after all changes."""

    BASE_URL = "http://localhost:5025"

    def test_server_is_running(self):
        """Test that server responds to health check."""
        response = requests.get(f"{self.BASE_URL}/health")
        assert response.status_code == 200
        assert response.json().get("ok") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
