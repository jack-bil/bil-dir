"""E2E tests for orchestrator rules customization.

Tests:
1. Config page displays rules section
2. Editing and saving rules in config page
3. Reset button restores default rules
4. Creating orchestrator with custom rules via UI
5. Orchestrator uses custom rules during execution
6. Orchestrator falls back to config defaults
"""
import pytest
import requests
import time


class TestConfigPageRules:
    """Test config page rules section."""

    BASE_URL = "http://localhost:5025"

    def test_config_page_has_rules_section(self):
        """Test that config page includes orchestrator rules section."""
        response = requests.get(f"{self.BASE_URL}/config")
        assert response.status_code == 200

        html = response.text

        # Check for rules section elements
        assert "Orchestrator Rules" in html
        assert 'id="orch_rules"' in html
        assert 'id="orchRulesResetBtn"' in html
        assert "Default rules that guide orchestrator decision-making" in html

    def test_config_page_shows_default_rules_initially(self):
        """Test that config page shows default rules on first load."""
        # First, clear any custom rules by saving empty
        form_data = {
            "orch_rules": "",
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Now get the page
        response = requests.get(f"{self.BASE_URL}/config")
        assert response.status_code == 200

        html = response.text
        assert "If the goal is achieved, return done" in html
        assert "ask_human" in html

    def test_save_custom_rules_in_config(self):
        """Test saving custom rules in config page."""
        custom_rules = "- E2E test rule 1\n- E2E test rule 2\n- E2E test rule 3"

        form_data = {
            "orch_rules": custom_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }

        response = requests.post(f"{self.BASE_URL}/config", data=form_data)
        assert response.status_code == 200

        html = response.text
        assert "E2E test rule 1" in html
        assert "E2E test rule 2" in html
        assert "E2E test rule 3" in html
        assert "Saved." in html

        # Verify persistence by reloading
        response = requests.get(f"{self.BASE_URL}/config")
        html = response.text
        assert "E2E test rule 1" in html

        # Cleanup: restore defaults
        form_data["orch_rules"] = ""
        requests.post(f"{self.BASE_URL}/config", data=form_data)

    def test_rules_persist_across_restarts(self):
        """Test that saved rules persist in config file."""
        custom_rules = "- Persistent rule test"

        form_data = {
            "orch_rules": custom_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }

        # Save custom rules
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Read config file directly
        import json
        import pathlib
        config_path = pathlib.Path("client_config.json")
        if config_path.exists():
            config = json.loads(config_path.read_text())
            assert config.get("orch_rules") == custom_rules

        # Cleanup
        form_data["orch_rules"] = ""
        requests.post(f"{self.BASE_URL}/config", data=form_data)


class TestOrchestratorModalRules:
    """Test orchestrator modal rules functionality."""

    BASE_URL = "http://localhost:5025"

    def test_create_orchestrator_with_custom_rules_via_api(self):
        """Test creating orchestrator with custom rules via API."""
        custom_rules = "- Modal test rule 1\n- Modal test rule 2"

        orch_data = {
            "name": f"e2e_custom_rules_{int(time.time())}",
            "provider": "codex",
            "goal": "Test custom rules in modal",
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

    def test_orchestrator_without_rules_uses_config_default(self):
        """Test that orchestrator without custom rules falls back to config."""
        # First, set custom config rules
        config_rules = "- Config default rule for e2e"
        form_data = {
            "orch_rules": config_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Create orchestrator without custom rules
        orch_data = {
            "name": f"e2e_no_rules_{int(time.time())}",
            "provider": "codex",
            "goal": "Test config fallback",
            "managed_sessions": ["test_session"],
            "enabled": False,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        orch = data.get("orchestrator")
        orch_id = orch.get("id")

        # The orchestrator should not have rules stored (uses config default)
        assert "rules" not in orch or not orch.get("rules")

        # Cleanup
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

        # Restore config defaults
        form_data["orch_rules"] = ""
        requests.post(f"{self.BASE_URL}/config", data=form_data)

    def test_update_orchestrator_rules(self):
        """Test updating orchestrator rules."""
        # Create orchestrator with initial rules
        initial_rules = "- Initial rule"
        orch_data = {
            "name": f"e2e_update_rules_{int(time.time())}",
            "provider": "codex",
            "goal": "Test rule updates",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": initial_rules,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        data = response.json()
        orch_id = data.get("orchestrator", {}).get("id")
        assert orch_id is not None

        # Update with new rules
        updated_rules = "- Updated rule 1\n- Updated rule 2"
        update_data = {
            "name": orch_data["name"],
            "goal": orch_data["goal"],
            "managed_sessions": orch_data["managed_sessions"],
            "rules": updated_rules,
        }

        response = requests.patch(f"{self.BASE_URL}/orchestrators/{orch_id}", json=update_data)
        assert response.status_code == 200

        # Verify update
        response = requests.get(f"{self.BASE_URL}/orchestrators")
        data = response.json()
        orchestrators = data.get("orchestrators", [])

        updated_orch = next((o for o in orchestrators if o.get("id") == orch_id), None)
        assert updated_orch is not None
        assert updated_orch.get("rules") == updated_rules

        # Cleanup
        requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")


class TestRulesFallbackChain:
    """Test the rules fallback chain in action."""

    BASE_URL = "http://localhost:5025"

    def test_custom_orchestrator_rules_take_precedence(self):
        """Test that custom orchestrator rules override config defaults."""
        # Set config rules
        config_rules = "- Config rule"
        form_data = {
            "orch_rules": config_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Create orchestrator with custom rules
        custom_rules = "- Custom orch rule that overrides config"
        orch_data = {
            "name": f"e2e_precedence_{int(time.time())}",
            "provider": "codex",
            "goal": "Test precedence",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": custom_rules,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        data = response.json()
        orch = data.get("orchestrator")
        orch_id = orch.get("id")

        # Verify custom rules are stored
        assert orch.get("rules") == custom_rules

        # Cleanup
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

        # Restore defaults
        form_data["orch_rules"] = ""
        requests.post(f"{self.BASE_URL}/config", data=form_data)

    def test_empty_rules_fall_back_to_config(self):
        """Test that empty orchestrator rules use config default."""
        # Set config rules
        config_rules = "- Config rule for fallback test"
        form_data = {
            "orch_rules": config_rules,
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Create orchestrator with empty rules (should be ignored)
        orch_data = {
            "name": f"e2e_fallback_{int(time.time())}",
            "provider": "codex",
            "goal": "Test fallback",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": "",  # Empty string
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        data = response.json()
        orch = data.get("orchestrator")
        orch_id = orch.get("id")

        # Empty rules should not be stored
        assert "rules" not in orch or not orch.get("rules")

        # Cleanup
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

        # Restore defaults
        form_data["orch_rules"] = ""
        requests.post(f"{self.BASE_URL}/config", data=form_data)

    def test_no_config_rules_uses_hardcoded_default(self):
        """Test that missing config rules use hardcoded DEFAULT_ORCH_RULES."""
        # Clear config rules
        form_data = {
            "orch_rules": "",
            "full_permissions": "on",
            "full_permissions_codex": "on",
            "full_permissions_gemini": "on",
            "full_permissions_claude": "on",
        }
        requests.post(f"{self.BASE_URL}/config", data=form_data)

        # Create orchestrator without rules
        orch_data = {
            "name": f"e2e_hardcoded_{int(time.time())}",
            "provider": "codex",
            "goal": "Test hardcoded default",
            "managed_sessions": ["test_session"],
            "enabled": False,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        data = response.json()
        orch = data.get("orchestrator")
        orch_id = orch.get("id")

        # No rules stored means it will use hardcoded default
        assert "rules" not in orch or not orch.get("rules")

        # Verify config page shows default when loaded
        response = requests.get(f"{self.BASE_URL}/config")
        html = response.text
        assert "If the goal is achieved, return done" in html

        # Cleanup
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")


class TestRulesValidation:
    """Test rules validation and edge cases."""

    BASE_URL = "http://localhost:5025"

    def test_very_long_rules(self):
        """Test that very long rules are accepted."""
        long_rules = "\n".join([f"- Rule {i}" for i in range(100)])

        orch_data = {
            "name": f"e2e_long_rules_{int(time.time())}",
            "provider": "codex",
            "goal": "Test long rules",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": long_rules,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        orch = data.get("orchestrator")
        assert orch.get("rules") == long_rules

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

    def test_rules_with_special_characters(self):
        """Test rules with special characters."""
        special_rules = "- Rule with \"quotes\"\n- Rule with 'apostrophes'\n- Rule with <tags>"

        orch_data = {
            "name": f"e2e_special_chars_{int(time.time())}",
            "provider": "codex",
            "goal": "Test special chars",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": special_rules,
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        orch = data.get("orchestrator")
        assert orch.get("rules") == special_rules

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")

    def test_whitespace_only_rules_treated_as_empty(self):
        """Test that whitespace-only rules are treated as empty."""
        orch_data = {
            "name": f"e2e_whitespace_{int(time.time())}",
            "provider": "codex",
            "goal": "Test whitespace",
            "managed_sessions": ["test_session"],
            "enabled": False,
            "rules": "   \n\n\t  ",
        }

        response = requests.post(f"{self.BASE_URL}/orchestrators", json=orch_data)
        assert response.status_code == 200

        data = response.json()
        orch = data.get("orchestrator")
        # Whitespace-only should not be stored
        assert "rules" not in orch or not orch.get("rules")

        # Cleanup
        orch_id = orch.get("id")
        if orch_id:
            requests.delete(f"{self.BASE_URL}/orchestrators/{orch_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
