"""Integration tests for API error responses."""
import pytest
import json


class TestStreamEndpointValidation:
    """Tests for /stream endpoint validation."""

    def test_missing_prompt_returns_error_code(self, client):
        """Should return 400 with INVALID_PROMPT code."""
        response = client.post('/stream',
                               json={"session_name": "test"},
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_PROMPT'

    def test_invalid_timeout_returns_error_code(self, client):
        """Should return 400 with INVALID_TIMEOUT code."""
        response = client.post('/stream',
                               json={
                                   "prompt": "test",
                                   "timeout_sec": 9999
                               },
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_TIMEOUT'

    def test_invalid_provider_returns_error_code(self, client):
        """Should return 400 with INVALID_PROVIDER code."""
        response = client.post('/stream',
                               json={
                                   "prompt": "test",
                                   "provider": "chatgpt"
                               },
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_PROVIDER'


class TestTasksEndpointValidation:
    """Tests for /tasks endpoint validation."""

    def test_get_nonexistent_task_returns_404(self, client):
        """Should return 404 with TASK_NOT_FOUND code."""
        response = client.get('/tasks/nonexistent')

        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'TASK_NOT_FOUND'

    def test_create_task_without_name_fails(self, client):
        """Should return 400 for missing name."""
        response = client.post('/tasks',
                               json={"prompt": "test"},
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_INPUT'

    def test_create_task_without_prompt_fails(self, client):
        """Should return 400 with INVALID_PROMPT code."""
        response = client.post('/tasks',
                               json={"name": "test"},
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_PROMPT'

    def test_create_task_with_invalid_schedule_fails(self, client):
        """Should return 400 with INVALID_SCHEDULE code."""
        response = client.post('/tasks',
                               json={
                                   "name": "test",
                                   "prompt": "test",
                                   "schedule": {"type": "yearly"}
                               },
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_SCHEDULE'


class TestOrchestratorsEndpointValidation:
    """Tests for /orchestrators endpoint validation."""

    def test_get_nonexistent_orchestrator_returns_404(self, client):
        """Should return 404 with ORCHESTRATOR_NOT_FOUND code."""
        response = client.patch('/orchestrators/nonexistent',
                                json={},
                                headers={'Content-Type': 'application/json'})

        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'ORCHESTRATOR_NOT_FOUND'

    def test_create_orchestrator_without_name_fails(self, client):
        """Should return 400 for missing name."""
        response = client.post('/orchestrators',
                               json={},
                               headers={'Content-Type': 'application/json'})

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert data.get('code') == 'INVALID_INPUT'


class TestErrorResponseFormat:
    """Tests for consistent error response format."""

    def test_all_errors_have_error_field(self, client):
        """All error responses should have 'error' field."""
        endpoints = [
            ('/stream', 'post', {}),
            ('/exec', 'post', {}),
            ('/tasks', 'post', {}),
        ]

        for url, method, data in endpoints:
            if method == 'post':
                response = client.post(url, json=data, headers={'Content-Type': 'application/json'})
            else:
                response = client.get(url)

            if response.status_code >= 400:
                data = response.get_json()
                assert 'error' in data, f"{url} missing 'error' field"

    def test_validation_errors_have_codes(self, client):
        """Validation errors should have error codes."""
        response = client.post('/stream',
                               json={},
                               headers={'Content-Type': 'application/json'})

        if response.status_code == 400:
            data = response.get_json()
            assert 'code' in data, "Validation error missing 'code' field"
