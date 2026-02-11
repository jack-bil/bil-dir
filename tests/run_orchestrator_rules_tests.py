"""Test runner for orchestrator rules customization tests.

Runs both unit and e2e tests for the orchestrator rules feature.
"""
import sys
import subprocess


def run_tests():
    """Run all orchestrator rules tests."""
    print("=" * 60)
    print("Running Orchestrator Rules Tests")
    print("=" * 60)
    print()

    # Run unit tests
    print("Running unit tests...")
    print("-" * 60)
    result_unit = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_orchestrator_rules.py", "-v", "--tb=short"],
        cwd="."
    )
    print()

    # Run e2e tests
    print("Running e2e tests...")
    print("-" * 60)
    result_e2e = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_orchestrator_rules_e2e.py", "-v", "--tb=short"],
        cwd="."
    )
    print()

    # Summary
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Unit tests: {'PASSED' if result_unit.returncode == 0 else 'FAILED'}")
    print(f"E2E tests: {'PASSED' if result_e2e.returncode == 0 else 'FAILED'}")
    print()

    # Exit with error if any tests failed
    if result_unit.returncode != 0 or result_e2e.returncode != 0:
        sys.exit(1)

    print("All tests passed!")


if __name__ == "__main__":
    run_tests()
