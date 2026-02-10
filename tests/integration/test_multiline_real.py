"""
Real multiline prompt execution tests.

These tests actually execute providers to verify multiline prompts work end-to-end.
Tests are automatically skipped if providers aren't available (no credits, not installed).

Run with: pytest tests/integration/test_multiline_real.py -v -s
Use -s flag to see provider output
"""
import pytest
import subprocess
import shutil


def check_provider_available(provider):
    """Check if a provider CLI is available and working."""
    if provider == 'codex':
        # Check if @agentic/codex is available
        result = subprocess.run(
            ['npx', '@agentic/codex', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    elif provider == 'copilot':
        # Check if gh copilot is available
        result = subprocess.run(
            ['gh', 'copilot', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    elif provider == 'claude':
        # Check if claude command is available
        if shutil.which('claude') is None:
            return False
        # Try running claude --version
        result = subprocess.run(
            ['claude', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    elif provider == 'gemini':
        # Check if gemini-cli is available
        result = subprocess.run(
            ['npx', 'gemini-cli', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    return False


# Check which providers are available at test collection time
CODEX_AVAILABLE = False
COPILOT_AVAILABLE = False
CLAUDE_AVAILABLE = False
GEMINI_AVAILABLE = False

try:
    CODEX_AVAILABLE = check_provider_available('codex')
except:
    pass

try:
    COPILOT_AVAILABLE = check_provider_available('copilot')
except:
    pass

try:
    CLAUDE_AVAILABLE = check_provider_available('claude')
except:
    pass

try:
    GEMINI_AVAILABLE = check_provider_available('gemini')
except:
    pass


@pytest.mark.slow
class TestRealMultilineExecution:
    """
    Real execution tests with actual provider CLIs.

    These tests are marked as 'slow' because they:
    - Make real API calls
    - Take 5-30 seconds per test
    - Require API credentials/credits
    - May fail due to network/quota issues

    Run only these tests: pytest -m slow
    Skip these tests: pytest -m "not slow"
    """

    @pytest.mark.skipif(not CODEX_AVAILABLE, reason="Codex not available or no credits")
    def test_codex_multiline_real_execution(self, client):
        """Codex should execute multiline prompt and return response."""
        import time

        multiline_prompt = """What is 2+2?
Then what is 3+3?"""

        print(f"\n[REAL TEST] Sending multiline prompt to Codex...")
        print(f"Prompt: {multiline_prompt!r}")

        response = client.post('/stream', json={
            'provider': 'codex',
            'prompt': multiline_prompt,
            'session_name': f'test_real_codex_{int(time.time())}',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200

        # Read SSE stream to verify execution
        data = response.get_data(as_text=True)
        print(f"\n[REAL TEST] Response preview: {data[:500]}")

        # Should contain actual response content
        # (This is the real test - did the provider actually respond?)
        assert len(data) > 100, "Response too short - provider may have failed"

    @pytest.mark.skipif(not COPILOT_AVAILABLE, reason="Copilot not available")
    def test_copilot_multiline_real_execution(self, client):
        """Copilot should execute multiline prompt and return response."""
        import time

        multiline_prompt = """List 3 Python features.
Keep it brief."""

        print(f"\n[REAL TEST] Sending multiline prompt to Copilot...")

        response = client.post('/stream', json={
            'provider': 'copilot',
            'prompt': multiline_prompt,
            'session_name': f'test_real_copilot_{int(time.time())}',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        data = response.get_data(as_text=True)
        print(f"\n[REAL TEST] Response preview: {data[:500]}")
        assert len(data) > 100

    @pytest.mark.skipif(not CLAUDE_AVAILABLE, reason="Claude not available")
    def test_claude_multiline_real_execution(self, client):
        """Claude should execute multiline prompt and return response."""
        import time

        multiline_prompt = """What is the capital of France?
Answer in one word."""

        print(f"\n[REAL TEST] Sending multiline prompt to Claude...")

        response = client.post('/stream', json={
            'provider': 'claude',
            'prompt': multiline_prompt,
            'session_name': f'test_real_claude_{int(time.time())}',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        data = response.get_data(as_text=True)
        print(f"\n[REAL TEST] Response preview: {data[:500]}")
        assert len(data) > 50

    @pytest.mark.skipif(not GEMINI_AVAILABLE, reason="Gemini not available")
    def test_gemini_multiline_real_execution(self, client):
        """Gemini should execute multiline prompt and return response."""
        import time

        multiline_prompt = """Count to 3.
Just numbers."""

        print(f"\n[REAL TEST] Sending multiline prompt to Gemini...")

        response = client.post('/stream', json={
            'provider': 'gemini',
            'prompt': multiline_prompt,
            'session_name': f'test_real_gemini_{int(time.time())}',
            'cwd': 'C:\\Users\\jackb\\Python_Projects\\bil-dir'
        })

        assert response.status_code == 200
        data = response.get_data(as_text=True)
        print(f"\n[REAL TEST] Response preview: {data[:500]}")
        assert len(data) > 50


def test_show_available_providers():
    """Show which providers are available for real testing."""
    print("\n" + "="*70)
    print("Provider Availability for Real Multiline Tests:")
    print("="*70)
    print(f"  Codex:   {'[YES] Available' if CODEX_AVAILABLE else '[NO] Not available'}")
    print(f"  Copilot: {'[YES] Available' if COPILOT_AVAILABLE else '[NO] Not available'}")
    print(f"  Claude:  {'[YES] Available' if CLAUDE_AVAILABLE else '[NO] Not available'}")
    print(f"  Gemini:  {'[YES] Available' if GEMINI_AVAILABLE else '[NO] Not available'}")
    print("="*70)

    if not any([CODEX_AVAILABLE, COPILOT_AVAILABLE, CLAUDE_AVAILABLE, GEMINI_AVAILABLE]):
        print("\n[WARNING] No providers available for real execution tests")
        print("   Real multiline tests will be skipped")
        print("\n   To enable real tests:")
        print("   1. Install provider CLIs")
        print("   2. Configure API keys/credentials")
        print("   3. Ensure quota/credits available")
    print()
