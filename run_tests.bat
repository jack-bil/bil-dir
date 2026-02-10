@echo off
REM Fast test runner for bil-dir
REM Usage: run_tests.bat [unit|integration|e2e|all|multiline|changed]

setlocal

set TEST_TYPE=%1
if "%TEST_TYPE%"=="" set TEST_TYPE=unit

echo.
echo ================================================
echo bil-dir Test Runner
echo ================================================
echo.

if "%TEST_TYPE%"=="unit" (
    echo Running unit tests only (fast - ~0.5s)...
    python -m pytest tests/unit/ -v
    goto :end
)

if "%TEST_TYPE%"=="integration" (
    echo Running integration tests (medium - ~2s)...
    python -m pytest tests/integration/ -v
    goto :end
)

if "%TEST_TYPE%"=="multiline" (
    echo Running multiline prompt tests for all providers (~1s)...
    python -m pytest tests/integration/test_multiline_prompts.py -v
    goto :end
)

if "%TEST_TYPE%"=="e2e" (
    echo Running E2E tests (slow - ~30s)...
    echo Starting app on port 5050...
    npx playwright test
    goto :end
)

if "%TEST_TYPE%"=="all" (
    echo Running all tests...
    echo.
    echo [1/3] Unit tests...
    python -m pytest tests/unit/ -v
    echo.
    echo [2/3] Integration tests...
    python -m pytest tests/integration/ -v
    echo.
    echo [3/3] E2E tests...
    npx playwright test
    goto :end
)

if "%TEST_TYPE%"=="changed" (
    echo Running only tests for changed files...
    REM Use git diff to find changed Python files
    for /f "delims=" %%i in ('git diff --name-only HEAD -- *.py') do (
        echo Changed: %%i
        REM Map file to test file and run it
        REM This is a simplified version - can be enhanced
    )
    goto :end
)

echo Unknown test type: %TEST_TYPE%
echo Usage: run_tests.bat [unit^|integration^|multiline^|e2e^|all^|changed]
exit /b 1

:end
echo.
echo ================================================
echo Tests complete!
echo ================================================
endlocal
