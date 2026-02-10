"""Integration tests for session management."""
import pytest
import json


class TestSessionCreation:
    """Test session creation across providers."""

    def test_create_codex_session_via_stream(self, client):
        """Should create new Codex session when none exists."""
        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': 'test',
            'session_name': 'pytest_codex_test',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })
        assert response.status_code == 200
        # SSE response will start streaming
        assert response.mimetype == 'text/event-stream'

    def test_create_copilot_session_via_stream(self, client):
        """Should create new Copilot session when none exists."""
        response = client.post('/stream', json={
            'provider': 'copilot',
            'prompt': 'test',
            'session_name': 'pytest_copilot_test',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })
        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'

    def test_create_claude_session_via_stream(self, client):
        """Should create new Claude session when none exists."""
        response = client.post('/stream', json={
            'provider': 'claude',
            'prompt': 'test',
            'session_name': 'pytest_claude_test',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })
        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'


class TestSessionValidation:
    """Test session input validation."""

    def test_invalid_session_name_returns_error(self, client):
        """Should reject invalid session names when provided."""
        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': 'test',
            'session_name': '   ',  # whitespace only
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_INPUT'

    def test_invalid_provider_returns_error(self, client):
        """Should reject invalid providers."""
        response = client.post('/stream', json={
            'provider': 'chatgpt',  # invalid
            'prompt': 'test',
            'session_name': 'test',
        })
        assert response.status_code == 400
        data = response.get_json()
        assert data.get('code') == 'INVALID_PROVIDER'

    def test_missing_prompt_returns_error(self, client):
        """Should reject requests without prompt."""
        response = client.post('/stream', json={
            'provider': 'codex',
            'session_name': 'test',
            # missing prompt
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
