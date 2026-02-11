# Orchestrator Rules Customization Tests

This document describes the test suite for the orchestrator rules customization feature.

## Overview

The orchestrator rules customization feature allows users to:
1. Edit default rules in the config page that apply to all orchestrators
2. Override rules for specific orchestrators in the orchestrator modal
3. Use a fallback chain: custom orchestrator rules → config default → hardcoded default

## Test Files

### Unit Tests (`tests/unit/test_orchestrator_rules.py`)

Tests the backend logic and API endpoints:

**TestOrchRulesConstants**
- ✓ DEFAULT_ORCH_RULES constant is defined
- ✓ Default rules contain expected content

**TestGetOrchestratorRules**
- ✓ Returns default when config is None
- ✓ Returns default when config is empty dict
- ✓ Returns default when orch_rules is empty string
- ✓ Returns default when orch_rules is whitespace
- ✓ Returns custom rules when provided
- ✓ Strips whitespace from custom rules

**TestConfigEndpoints**
- ✓ GET /config includes orch_rules in response
- ✓ POST /config saves orch_rules
- ✓ POST /config handles empty orch_rules

**TestOrchestratorCreation**
- ✓ Create orchestrator with custom rules
- ✓ Create orchestrator without rules
- ✓ Create orchestrator with empty rules string

### E2E Tests (`tests/integration/test_orchestrator_rules_e2e.py`)

Tests the full flow through UI and backend:

**TestConfigPageRules**
- ✓ Config page has rules section
- ✓ Config page shows default rules initially
- ✓ Save custom rules in config
- ✓ Rules persist across restarts

**TestOrchestratorModalRules**
- ✓ Create orchestrator with custom rules via API
- ✓ Orchestrator without rules uses config default
- ✓ Update orchestrator rules

**TestRulesFallbackChain**
- ✓ Custom orchestrator rules take precedence over config
- ✓ Empty rules fall back to config
- ✓ No config rules uses hardcoded default

**TestRulesValidation**
- ✓ Very long rules are accepted
- ✓ Rules with special characters work
- ✓ Whitespace-only rules treated as empty

## Running the Tests

### Run All Tests
```bash
python tests/run_orchestrator_rules_tests.py
```

### Run Unit Tests Only
```bash
pytest tests/unit/test_orchestrator_rules.py -v
```

### Run E2E Tests Only
```bash
pytest tests/integration/test_orchestrator_rules_e2e.py -v
```

### Run Specific Test Class
```bash
pytest tests/unit/test_orchestrator_rules.py::TestGetOrchestratorRules -v
```

### Run Specific Test
```bash
pytest tests/unit/test_orchestrator_rules.py::TestGetOrchestratorRules::test_returns_custom_rules_when_provided -v
```

## Prerequisites

1. **Server must be running**: The tests require the bil-dir server to be running on `http://localhost:5025`
2. **pytest installed**: `pip install pytest`
3. **requests library**: `pip install requests`

## Test Coverage

The test suite covers:

- ✅ Backend helper functions (`_get_orchestrator_rules`)
- ✅ Config page GET/POST endpoints
- ✅ Orchestrator creation with custom rules
- ✅ Rules fallback chain (custom → config → hardcoded)
- ✅ Edge cases (empty strings, whitespace, special characters)
- ✅ Persistence across requests
- ✅ Update operations

## Expected Behavior

### Fallback Chain

1. **Custom Orchestrator Rules** (highest priority)
   - If an orchestrator has `rules` field set, use those rules
   - Example: `{"id": "123", "rules": "- Custom rule"}`

2. **Config Default Rules** (medium priority)
   - If orchestrator has no custom rules, use `config.orch_rules`
   - Set via `/config` page or `client_config.json`

3. **Hardcoded Default** (lowest priority)
   - If config has no rules, use `DEFAULT_ORCH_RULES` constant
   - Defined in `utils/config.py`

### Storage Rules

- Empty strings (`""`) are NOT stored
- Whitespace-only strings are NOT stored
- Only non-empty, meaningful rules are persisted

## Troubleshooting

### Tests Fail with Connection Error
- Make sure the bil-dir server is running on port 5025
- Check that the server started without errors

### Tests Fail with Import Error
- Make sure pytest is installed: `pip install pytest`
- Make sure requests is installed: `pip install requests`
- Run tests from the project root directory

### Tests Leave Data Behind
- The tests include cleanup code to remove test orchestrators
- If tests crash, you may need to manually clean up test data
- Look for orchestrators with names starting with `e2e_` or `test_`

## Contributing

When adding new orchestrator rules features, please:
1. Add unit tests for backend logic
2. Add e2e tests for user-facing functionality
3. Update this README with new test coverage
4. Run the full test suite before committing

## Related Files

- `utils/config.py` - DEFAULT_ORCH_RULES, _get_orchestrator_rules()
- `app.py` - Config endpoints, orchestrator creation, _run_orchestrator_decision()
- `templates/config.html` - Config page UI with rules section
- `templates/chat.html` - Orchestrator modal with rules field
