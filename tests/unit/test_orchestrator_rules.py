"""Unit tests for orchestrator rules customization.

Tests:
1. DEFAULT_ORCH_RULES constant is defined
2. _get_orchestrator_rules() returns correct values
3. Config endpoints handle orch_rules correctly
4. Orchestrator creation stores custom rules
5. Orchestrator decision logic uses rules fallback chain
"""
import pytest
import json
import time
from utils.config import (
    DEFAULT_ORCH_RULES,
    _get_orchestrator_rules,
)


class TestOrchRulesConstants:
    """Test that orchestrator rules constants are defined correctly."""

    def test_default_orch_rules_is_defined(self):
        """Test that DEFAULT_ORCH_RULES constant exists and is non-empty."""
        assert DEFAULT_ORCH_RULES is not None
        assert isinstance(DEFAULT_ORCH_RULES, str)
        assert len(DEFAULT_ORCH_RULES) > 0

    def test_default_orch_rules_contains_expected_rules(self):
        """Test that default rules contain expected content."""
        assert "If the goal is achieved, return done" in DEFAULT_ORCH_RULES
        assert "ask_human" in DEFAULT_ORCH_RULES
        assert "conversation history" in DEFAULT_ORCH_RULES


class TestGetOrchestratorRules:
    """Test _get_orchestrator_rules() helper function."""

    def test_returns_default_when_config_is_none(self):
        """Test that function returns default when config is None."""
        result = _get_orchestrator_rules(None)
        assert result == DEFAULT_ORCH_RULES

    def test_returns_default_when_config_is_empty_dict(self):
        """Test that function returns default when config is empty dict."""
        result = _get_orchestrator_rules({})
        assert result == DEFAULT_ORCH_RULES

    def test_returns_default_when_orch_rules_is_empty_string(self):
        """Test that function returns default when orch_rules is empty."""
        config = {"orch_rules": ""}
        result = _get_orchestrator_rules(config)
        assert result == DEFAULT_ORCH_RULES

    def test_returns_default_when_orch_rules_is_whitespace(self):
        """Test that function returns default when orch_rules is whitespace."""
        config = {"orch_rules": "   \n  \t  "}
        result = _get_orchestrator_rules(config)
        assert result == DEFAULT_ORCH_RULES

    def test_returns_custom_rules_when_provided(self):
        """Test that function returns custom rules when provided."""
        custom_rules = "- Custom rule 1\n- Custom rule 2"
        config = {"orch_rules": custom_rules}
        result = _get_orchestrator_rules(config)
        assert result == custom_rules

    def test_strips_whitespace_from_custom_rules(self):
        """Test that function strips whitespace from custom rules."""
        custom_rules = "  - Custom rule  \n  - Another rule  "
        config = {"orch_rules": custom_rules}
        result = _get_orchestrator_rules(config)
        assert result == custom_rules.strip()


class TestConfigEndpoints:
    """Test that config endpoints handle orch_rules correctly."""

    BASE_URL = "http://localhost:5025"

    def test_get_config_includes_orch_rules(self):
        """Test that GET /config includes orch_rules in response."""
        import requests
        response = requests.get(f"{self.BASE_URL}/config")
        assert response.status_code == 200

        # Check that the textarea for orch_rules exists
        html = response.text
        assert 'id="orch_rules"' in html
        assert 'name="orch_rules"' in html
        assert 'Orchestrator Rules' in html

    def test_post_config_saves_orch_rules(self):
        """Test that POST /config saves orch_rules."""
        import requests

        # First, get current config
        response = requests.get(f"{self.BASE_URL}/config")
        assert response.status_code == 200

        # Save with custom rules
        custom_rules = "- Test rule 1\n- Test rule 2"
        form_data = {
            "orch_rules": custom_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }

        response = requests.post(f"{self.BASE_URL}/config", data=form_data)
        assert response.status_code == 200

        # Verify the rules are in the response
        html = response.text
        assert "Test rule 1" in html
        assert "Test rule 2" in html

    def test_post_config_saves_empty_orch_rules(self):
        """Test that POST /config handles empty orch_rules."""
        import requests

        form_data = {
            "orch_rules": "",
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }

        response = requests.post(f"{self.BASE_URL}/config", data=form_data)
        assert response.status_code == 200

        # Should show default rules when empty
        html = response.text
        assert "If the goal is achieved, return done" in html


class TestOrchestratorCreation:
    """Test that orchestrator creation handles custom rules."""

    BASE_URL = "http://localhost:5025"

    def test_create_orchestrator_with_custom_rules(self):
        """Test creating an orchestrator with custom rules."""
        import requests

        custom_rules = "- Custom orchestrator rule\n- Another custom rule"

        orch_data = {
            "name": f"test_rules_orch_{int(time.time())}",
            "provider": "codex",
            "goal": "Test custom rules",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": custom_rules,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        assert data.get("ok") is True

        orch = data.get("orchestrator")
        assert orch is not None
        assert orch.get("rules") == custom_rules

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

    def test_create_orchestrator_without_rules(self):
        """Test creating an orchestrator without custom rules."""
        import requests

        orch_data = {
            "name": f"test_no_rules_orch_{int(time.time())}",
            "provider": "codex",
            "goal": "Test default rules",
            "managed_sessions": ["test_session"],
            "enabled": False,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        assert data.get("ok") is True

        orch = data.get("orchestrator")
        assert orch is not None
        # Should not have rules field if not provided
        assert "rules" not in orch or not orch.get("rules")

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

    def test_create_orchestrator_with_empty_rules(self):
        """Test creating an orchestrator with empty rules string."""
        import requests

        orch_data = {
            "name": f"test_empty_rules_orch_{int(time.time())}",
            "provider": "codex",
            "goal": "Test empty rules",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": "",
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        assert data.get("ok") is True

        orch = data.get("orchestrator")
        assert orch is not None
        # Empty string should not be stored
        assert "rules" not in orch or not orch.get("rules")

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
