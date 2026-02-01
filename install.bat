@echo off
REM Bil-dir Installer Launcher
REM This launches the PowerShell installer with proper execution policy

echo ===================================================
echo   Bil-dir Orchestrator - Windows Installer
echo ===================================================
echo.
echo This will install Python (if needed) and set up Bil-dir.
echo.
echo NOTE: You may need to run this as Administrator
echo       for Python installation to succeed.
echo.
pause

REM Run PowerShell installer with bypass execution policy
PowerShell -ExecutionPolicy Bypass -File "%~dp0install.ps1"

if errorlevel 1 (
    echo.
    echo Installation encountered an error.
    echo.
    pause
    exit /b 1
)

echo.
echo Installation completed successfully!
pause
