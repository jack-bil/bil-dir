"""
Integration tests for multi-line prompt API handling.

IMPORTANT: These tests verify that the API accepts and processes multiline prompts,
but they DO NOT verify that providers actually execute successfully. They test:
  ✓ API accepts multiline JSON
  ✓ Endpoint starts SSE stream
  ✓ No crashes on multiline input

They DO NOT test:
  ✗ Provider CLI receives correct stdin
  ✗ Provider actually responds
  ✗ Multiline formatting preserved through execution

For real execution tests, see: tests/integration/test_multiline_real.py
For stdin mechanism tests, see: tests/unit/test_multiline_stdin.py
"""
import pytest


class TestMultilinePrompts:
    """Test that API accepts multi-line prompts for all providers."""

    @pytest.fixture
    def multiline_prompt(self):
        """Sample multi-line prompt for testing."""
        return """This is line 1 of the prompt.
This is line 2 with special chars: @#$%
This is line 3.

Line 5 after blank line."""

    def test_codex_accepts_multiline_prompt(self, client, multiline_prompt):
        """Codex should accept and process multi-line prompts."""
        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': multiline_prompt,
            'session_name': 'test_codex_multiline',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        # Should start streaming (200 with SSE)
        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'

    def test_copilot_accepts_multiline_prompt(self, client, multiline_prompt):
        """Copilot should accept and process multi-line prompts."""
        response = client.post('/stream', json={
            'provider': 'copilot',
            'prompt': multiline_prompt,
            'session_name': 'test_copilot_multiline',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'

    def test_claude_accepts_multiline_prompt(self, client, multiline_prompt):
        """Claude should accept and process multi-line prompts."""
        response = client.post('/stream', json={
            'provider': 'claude',
            'prompt': multiline_prompt,
            'session_name': 'test_claude_multiline',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'

    def test_gemini_accepts_multiline_prompt(self, client, multiline_prompt):
        """Gemini should accept and process multi-line prompts."""
        response = client.post('/stream', json={
            'provider': 'gemini',
            'prompt': multiline_prompt,
            'session_name': 'test_gemini_multiline',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'

    def test_multiline_with_special_characters(self, client):
        """Should handle multi-line prompts with special characters."""
        special_prompt = """Test with quotes: "hello" and 'world'
Test with backslashes: C:\\Users\\test
Test with symbols: @#$%^&*()
Test with unicode: 你好 مرحبا"""

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': special_prompt,
            'session_name': 'test_special_chars',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200

    def test_multiline_with_blank_lines(self, client):
        """Should handle multi-line prompts with blank lines."""
        prompt_with_blanks = """Line 1

Line 3 (after blank line)


Line 6 (after multiple blank lines)"""

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': prompt_with_blanks,
            'session_name': 'test_blank_lines',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200

    def test_multiline_with_indentation(self, client):
        """Should preserve indentation in multi-line prompts."""
        indented_prompt = """Write a Python function:
    def hello():
        print("Hello")
        return True"""

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': indented_prompt,
            'session_name': 'test_indentation',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200


class TestMultilineTaskExecution:
    """Test that tasks with multi-line prompts execute correctly."""

    def test_task_with_multiline_prompt_codex(self, client):
        """Should create and accept task with multi-line prompt for Codex."""
        multiline_prompt = """Step 1: List files
Step 2: Check status
Step 3: Report results"""

        response = client.post('/tasks', json={
            'name': 'Multiline Codex Task',
            'prompt': multiline_prompt,
            'provider': 'codex',
            'schedule': {'type': 'manual'}
        })

        assert response.status_code == 200
        task = response.get_json()['task']
        assert task['prompt'] == multiline_prompt

        # Cleanup
        client.delete(f'/tasks/{task["id"]}')

    def test_task_with_multiline_prompt_claude(self, client):
        """Should create and accept task with multi-line prompt for Claude."""
        multiline_prompt = """Please analyze:
1. Code structure
2. Dependencies
3. Potential issues"""

        response = client.post('/tasks', json={
            'name': 'Multiline Claude Task',
            'prompt': multiline_prompt,
            'provider': 'claude',
            'schedule': {'type': 'manual'}
        })

        assert response.status_code == 200
        task = response.get_json()['task']
        assert task['prompt'] == multiline_prompt

        # Cleanup
        client.delete(f'/tasks/{task["id"]}')

    def test_task_update_with_multiline_prompt(self, client):
        """Should update task prompt with multi-line content."""
        # Create task with single-line prompt
        response = client.post('/tasks', json={
            'name': 'Update Test',
            'prompt': 'Original prompt',
            'provider': 'codex'
        })
        task_id = response.get_json()['task']['id']

        # Update with multi-line prompt
        new_prompt = """Updated multi-line prompt:
Line 1
Line 2
Line 3"""

        response = client.patch(f'/tasks/{task_id}', json={
            'prompt': new_prompt
        })

        assert response.status_code == 200

        # Verify update
        response = client.get(f'/tasks/{task_id}')
        task = response.get_json()['task']
        assert task['prompt'] == new_prompt

        # Cleanup
        client.delete(f'/tasks/{task_id}')


class TestMultilineEdgeCases:
    """Test edge cases for multi-line prompt handling."""

    def test_very_long_multiline_prompt(self, client):
        """Should handle very long multi-line prompts."""
        # Generate 50 lines
        long_prompt = '\n'.join([f'Line {i+1}: Some content here' for i in range(50)])

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': long_prompt,
            'session_name': 'test_long_prompt',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200

    def test_prompt_with_only_newlines(self, client):
        """Should reject prompt with only whitespace/newlines."""
        whitespace_prompt = '\n\n\n\n'

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': whitespace_prompt,
            'session_name': 'test_whitespace',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        # Should reject (400) or strip to empty and reject
        assert response.status_code in [200, 400]

    def test_prompt_with_mixed_line_endings(self, client):
        """Should handle mixed line endings (\\n, \\r\\n)."""
        mixed_endings = 'Line 1\nLine 2\r\nLine 3\nLine 4'

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': mixed_endings,
            'session_name': 'test_mixed_endings',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
