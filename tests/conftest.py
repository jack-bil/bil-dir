"""Shared test fixtures for pytest."""
import sys
import os
import pytest
import tempfile
import shutil

# Add parent directory to path so we can import from project
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_task():
    """Sample task data for testing."""
    return {
        "id": "test123",
        "name": "Test Task",
        "prompt": "Do something",
        "provider": "codex",
        "schedule": {"type": "manual"},
        "enabled": True,
        "created_at": "2026-01-01T00:00:00"
    }


@pytest.fixture
def sample_orchestrator():
    """Sample orchestrator data for testing."""
    return {
        "id": "orch123",
        "name": "Test Orchestrator",
        "provider": "codex",
        "managed_sessions": ["session1", "session2"],
        "goal": "Test goal",
        "enabled": True,
        "history": []
    }


@pytest.fixture
def sample_session():
    """Sample session data for testing."""
    return {
        "session_id": "sess123",
        "session_ids": {"codex": "sess123"},
        "provider": "codex",
        "last_used": "2026-01-01T00:00:00",
        "created_at": "2026-01-01T00:00:00"
    }


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from app import APP
    APP.config['TESTING'] = True
    return APP


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()
