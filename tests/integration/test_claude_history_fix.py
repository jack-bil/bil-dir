"""
Integration test for Claude history reading fix.

Tests that orchestrators can read Claude session history from JSONL files.
This test creates a real Claude JSONL file and verifies it can be read.
"""
import pytest
import json
import os
import tempfile
import shutil


class TestClaudeHistoryFix:
    """Test the fix for orchestrators reading Claude session history."""

    def test_get_claude_history_reads_jsonl_file(self):
        """Should read messages from Claude JSONL session file."""
        from app import _get_claude_history

        # Create temporary directory structure mimicking Claude's storage
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "test-uuid-12345"
            workdir = os.path.join(tmp_dir, "test_project")
            os.makedirs(workdir, exist_ok=True)

            # Claude converts workdir to safe name: C:/Users/test -> C--Users-test
            safe_workdir = workdir.replace(":", "-").replace("\\", "-").replace("/", "-")
            if safe_workdir.startswith("-"):
                safe_workdir = safe_workdir[1:]

            # Create Claude projects directory structure
            claude_dir = os.path.join(tmp_dir, ".claude", "projects", safe_workdir)
            os.makedirs(claude_dir, exist_ok=True)

            # Create Claude JSONL session file
            session_file = os.path.join(claude_dir, f"{session_id}.jsonl")

            with open(session_file, 'w', encoding='utf-8') as f:
                # Write user message
                f.write(json.dumps({
                    "type": "user",
                    "message": {"content": "What is 2+2?"},
                    "uuid": "user-uuid-1",
                    "timestamp": "2026-02-09T10:00:00.000Z"
                }) + "\n")

                # Write assistant message
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "2+2 equals 4."}
                        ]
                    },
                    "uuid": "assistant-uuid-1",
                    "timestamp": "2026-02-09T10:00:01.000Z"
                }) + "\n")

                # Write another user message
                f.write(json.dumps({
                    "type": "user",
                    "message": {"content": "And 3+3?"},
                    "uuid": "user-uuid-2",
                    "timestamp": "2026-02-09T10:00:02.000Z"
                }) + "\n")

                # Write another assistant message
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "3+3 equals 6."}
                        ]
                    },
                    "uuid": "assistant-uuid-2",
                    "timestamp": "2026-02-09T10:00:03.000Z"
                }) + "\n")

            # Mock os.path.expanduser to return our tmp_dir
            import app
            original_expanduser = os.path.expanduser
            os.path.expanduser = lambda p: tmp_dir if p == "~" else original_expanduser(p)

            try:
                # Test reading history
                history = _get_claude_history(session_id, workdir)

                # Verify history structure
                assert "messages" in history
                assert "tool_outputs" in history
                assert isinstance(history["messages"], list)
                assert isinstance(history["tool_outputs"], list)

                # Verify message count
                assert len(history["messages"]) == 4, f"Expected 4 messages, got {len(history['messages'])}"

                # Verify first message (user)
                msg1 = history["messages"][0]
                assert msg1["role"] == "user"
                assert msg1["text"] == "What is 2+2?"

                # Verify second message (assistant)
                msg2 = history["messages"][1]
                assert msg2["role"] == "assistant"
                assert msg2["text"] == "2+2 equals 4."

                # Verify third message (user)
                msg3 = history["messages"][2]
                assert msg3["role"] == "user"
                assert msg3["text"] == "And 3+3?"

                # Verify fourth message (assistant)
                msg4 = history["messages"][3]
                assert msg4["role"] == "assistant"
                assert msg4["text"] == "3+3 equals 6."

                print("\n[PASS] Successfully read 4 messages from Claude JSONL file")

            finally:
                os.path.expanduser = original_expanduser

    def test_get_history_for_name_uses_claude_reader_for_claude_sessions(self):
        """Should use Claude JSONL reader when provider is 'claude'."""
        from app import _get_history_for_name, _load_sessions, _save_sessions

        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "test-claude-session-uuid"
            session_name = "test_claude_session"
            workdir = os.path.join(tmp_dir, "claude_project")
            os.makedirs(workdir, exist_ok=True)

            # Create Claude session JSONL file
            safe_workdir = workdir.replace(":", "-").replace("\\", "-").replace("/", "-")
            if safe_workdir.startswith("-"):
                safe_workdir = safe_workdir[1:]

            claude_dir = os.path.join(tmp_dir, ".claude", "projects", safe_workdir)
            os.makedirs(claude_dir, exist_ok=True)

            session_file = os.path.join(claude_dir, f"{session_id}.jsonl")
            with open(session_file, 'w', encoding='utf-8') as f:
                f.write(json.dumps({
                    "type": "user",
                    "message": {"content": "Hello from test!"},
                    "uuid": "test-uuid"
                }) + "\n")
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Hi there!"}]
                    },
                    "uuid": "test-uuid-2"
                }) + "\n")

            # Register session in sessions.json
            sessions = _load_sessions()
            sessions[session_name] = {
                "session_id": session_id,
                "session_ids": {"claude": session_id},
                "provider": "claude",
                "workdir": workdir,
                "created_at": "2026-02-09T10:00:00",
                "last_used": "2026-02-09T10:00:01"
            }
            _save_sessions(sessions)

            # Mock os.path.expanduser
            import app
            original_expanduser = os.path.expanduser
            os.path.expanduser = lambda p: tmp_dir if p == "~" else original_expanduser(p)

            try:
                # Test _get_history_for_name with Claude session
                history = _get_history_for_name(session_name)

                # Should have read from JSONL file
                assert len(history["messages"]) == 2
                assert history["messages"][0]["text"] == "Hello from test!"
                assert history["messages"][1]["text"] == "Hi there!"

                print("\n[PASS] _get_history_for_name correctly reads Claude JSONL files")

            finally:
                os.path.expanduser = original_expanduser
                # Cleanup session
                sessions = _load_sessions()
                if session_name in sessions:
                    del sessions[session_name]
                    _save_sessions(sessions)

    def test_claude_history_with_multi_line_messages(self):
        """Should handle multi-line messages in Claude sessions."""
        from app import _get_claude_history

        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "multiline-test-uuid"
            workdir = os.path.join(tmp_dir, "multiline_project")

            safe_workdir = workdir.replace(":", "-").replace("\\", "-").replace("/", "-")
            if safe_workdir.startswith("-"):
                safe_workdir = safe_workdir[1:]

            claude_dir = os.path.join(tmp_dir, ".claude", "projects", safe_workdir)
            os.makedirs(claude_dir, exist_ok=True)

            session_file = os.path.join(claude_dir, f"{session_id}.jsonl")
            with open(session_file, 'w', encoding='utf-8') as f:
                # Multi-line user message
                f.write(json.dumps({
                    "type": "user",
                    "message": {
                        "content": "Line 1\nLine 2\nLine 3"
                    },
                    "uuid": "user-multiline"
                }) + "\n")

                # Multi-block assistant message
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Response part 1"},
                            {"type": "text", "text": "Response part 2"}
                        ]
                    },
                    "uuid": "assistant-multiblock"
                }) + "\n")

            import app
            original_expanduser = os.path.expanduser
            os.path.expanduser = lambda p: tmp_dir if p == "~" else original_expanduser(p)

            try:
                history = _get_claude_history(session_id, workdir)

                # Multi-line should be preserved
                assert history["messages"][0]["text"] == "Line 1\nLine 2\nLine 3"

                # Multi-block should be joined with newlines
                assert history["messages"][1]["text"] == "Response part 1\nResponse part 2"

                print("\n[PASS] Multi-line messages handled correctly")

            finally:
                os.path.expanduser = original_expanduser

    def test_empty_claude_session_returns_empty_history(self):
        """Should return empty history for non-existent Claude session."""
        from app import _get_claude_history

        history = _get_claude_history("nonexistent-uuid", "/fake/path")

        assert history["messages"] == []
        assert history["tool_outputs"] == []

        print("\n[PASS] Non-existent session returns empty history")

    def test_consecutive_assistant_messages_are_merged(self):
        """Should merge consecutive assistant messages into single bubbles."""
        from app import _get_claude_history

        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "merge-test-uuid"
            workdir = os.path.join(tmp_dir, "merge_project")
            os.makedirs(workdir, exist_ok=True)

            safe_workdir = workdir.replace(":", "-").replace("\\", "-").replace("/", "-")
            if safe_workdir.startswith("-"):
                safe_workdir = safe_workdir[1:]

            claude_dir = os.path.join(tmp_dir, ".claude", "projects", safe_workdir)
            os.makedirs(claude_dir, exist_ok=True)

            session_file = os.path.join(claude_dir, f"{session_id}.jsonl")
            with open(session_file, 'w', encoding='utf-8') as f:
                # User message
                f.write(json.dumps({
                    "type": "user",
                    "message": {"content": "Hello"},
                    "uuid": "user-1"
                }) + "\n")

                # First assistant message
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Part 1"}]
                    },
                    "uuid": "assistant-1"
                }) + "\n")

                # Second consecutive assistant message (should merge)
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Part 2"}]
                    },
                    "uuid": "assistant-2"
                }) + "\n")

                # Third consecutive assistant message (should merge)
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Part 3"}]
                    },
                    "uuid": "assistant-3"
                }) + "\n")

                # Another user message
                f.write(json.dumps({
                    "type": "user",
                    "message": {"content": "Thanks"},
                    "uuid": "user-2"
                }) + "\n")

                # Another assistant message (separate bubble)
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "You're welcome"}]
                    },
                    "uuid": "assistant-4"
                }) + "\n")

            import app
            original_expanduser = os.path.expanduser
            os.path.expanduser = lambda p: tmp_dir if p == "~" else original_expanduser(p)

            try:
                history = _get_claude_history(session_id, workdir)

                # Should have 4 messages total: user, merged assistant, user, assistant
                assert len(history["messages"]) == 4, f"Expected 4 messages, got {len(history['messages'])}"

                # First message: user
                assert history["messages"][0]["role"] == "user"
                assert history["messages"][0]["text"] == "Hello"

                # Second message: merged assistant (Parts 1, 2, 3)
                assert history["messages"][1]["role"] == "assistant"
                assert history["messages"][1]["text"] == "Part 1\nPart 2\nPart 3", \
                    f"Expected merged text, got: {history['messages'][1]['text']}"

                # Third message: user
                assert history["messages"][2]["role"] == "user"
                assert history["messages"][2]["text"] == "Thanks"

                # Fourth message: separate assistant
                assert history["messages"][3]["role"] == "assistant"
                assert history["messages"][3]["text"] == "You're welcome"

                print("\n[PASS] Consecutive assistant messages merged correctly into single bubble")

            finally:
                os.path.expanduser = original_expanduser


if __name__ == "__main__":
    # Allow running directly for quick testing
    print("Running Claude history fix tests...\n")

    test = TestClaudeHistoryFix()

    try:
        print("[1/5] Testing Claude JSONL file reading...")
        test.test_get_claude_history_reads_jsonl_file()

        print("\n[2/5] Testing _get_history_for_name with Claude provider...")
        test.test_get_history_for_name_uses_claude_reader_for_claude_sessions()

        print("\n[3/5] Testing multi-line message handling...")
        test.test_claude_history_with_multi_line_messages()

        print("\n[4/5] Testing empty session handling...")
        test.test_empty_claude_session_returns_empty_history()

        print("\n[5/5] Testing consecutive assistant message merging...")
        test.test_consecutive_assistant_messages_are_merged()

        print("\n" + "="*70)
        print("[PASS] ALL TESTS PASSED!")
        print("="*70)
        print("\nThe Claude history fix is working correctly.")
        print("Orchestrators can now read Claude session history from JSONL files.")
        print("Consecutive assistant messages are properly merged into single bubbles.")

    except AssertionError as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n[FAIL] ERROR: {e}")
        import traceback
        traceback.print_exc()
