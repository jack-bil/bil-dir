# Bil-dir Installer for Windows
# This script installs Python (if needed) and sets up the Bil-dir orchestrator

param(
    [switch]$SkipPythonInstall,
    [string]$PythonVersion = "3.11.9",
    [int]$Port = 5025
)

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Bil-dir Orchestrator - Windows Installer" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "WARNING: Not running as Administrator. Python installation may fail." -ForegroundColor Yellow
    Write-Host "Consider running: Start-Process powershell -Verb RunAs -ArgumentList '-File install.ps1'" -ForegroundColor Yellow
    Write-Host ""
}

# Function to check Python installation
function Test-PythonInstalled {
    try {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $version = & python --version 2>&1
            Write-Host "✓ Python found: $version" -ForegroundColor Green
            return $true
        }
    } catch {}
    
    # Try python3
    try {
        $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $version = & python3 --version 2>&1
            Write-Host "✓ Python3 found: $version" -ForegroundColor Green
            return $true
        }
    } catch {}
    
    return $false
}

# Function to install Python silently
function Install-Python {
    param([string]$Version)
    
    Write-Host "Downloading Python $Version installer..." -ForegroundColor Yellow
    
    $installerUrl = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
    $installerPath = "$env:TEMP\python-$Version-installer.exe"
    
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-Host "✓ Downloaded Python installer" -ForegroundColor Green
    } catch {
        Write-Host "✗ Failed to download Python installer: $_" -ForegroundColor Red
        Write-Host "Please download and install Python manually from: https://www.python.org/downloads/" -ForegroundColor Yellow
        exit 1
    }
    
    Write-Host "Installing Python $Version silently..." -ForegroundColor Yellow
    Write-Host "(This may take a few minutes)" -ForegroundColor Gray
    
    # Silent install with all features
    $installArgs = @(
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
        "Include_pip=1",
        "Include_doc=0"
    )
    
    $process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
    
    if ($process.ExitCode -eq 0) {
        Write-Host "✓ Python installed successfully" -ForegroundColor Green
        
        # Refresh environment variables
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        
        # Clean up installer
        Remove-Item $installerPath -Force
    } else {
        Write-Host "✗ Python installation failed with exit code: $($process.ExitCode)" -ForegroundColor Red
        exit 1
    }
}

# Step 1: Check/Install Python
Write-Host "[1/4] Checking Python installation..." -ForegroundColor Cyan
if (Test-PythonInstalled) {
    Write-Host "Python is already installed." -ForegroundColor Green
} elseif ($SkipPythonInstall) {
    Write-Host "✗ Python not found and -SkipPythonInstall specified." -ForegroundColor Red
    exit 1
} else {
    Install-Python -Version $PythonVersion
    
    # Verify installation
    if (-not (Test-PythonInstalled)) {
        Write-Host "✗ Python installation verification failed." -ForegroundColor Red
        Write-Host "Please restart your PowerShell session or computer and try again." -ForegroundColor Yellow
        exit 1
    }
}
Write-Host ""

# Step 2: Install Python dependencies
Write-Host "[2/4] Installing Python dependencies..." -ForegroundColor Cyan
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$requirementsFile = Join-Path $scriptDir "requirements.txt"

if (Test-Path $requirementsFile) {
    try {
        & python -m pip install --upgrade pip --quiet
        & python -m pip install -r $requirementsFile --quiet
        Write-Host "✓ Dependencies installed" -ForegroundColor Green
    } catch {
        Write-Host "✗ Failed to install dependencies: $_" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "⚠ requirements.txt not found, skipping..." -ForegroundColor Yellow
}
Write-Host ""

# Step 3: Create startup script
Write-Host "[3/4] Creating startup script..." -ForegroundColor Cyan
$startScript = Join-Path $scriptDir "start.bat"
$startContent = @"
@echo off
cd /d "%~dp0"
echo Starting Bil-dir Orchestrator...
echo.
echo Server will be available at: http://localhost:$Port
echo Press Ctrl+C to stop the server
echo.
python app.py
pause
"@

Set-Content -Path $startScript -Value $startContent -Force
Write-Host "✓ Created start.bat" -ForegroundColor Green
Write-Host ""

# Step 4: Create desktop shortcut (optional)
Write-Host "[4/4] Creating desktop shortcut..." -ForegroundColor Cyan
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $desktop = [System.Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop "Bil-dir.lnk"
    $shortcut = $WshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $startScript
    $shortcut.WorkingDirectory = $scriptDir
    $shortcut.Description = "Bil-dir AI Orchestrator"
    $shortcut.Save()
    Write-Host "✓ Desktop shortcut created" -ForegroundColor Green
} catch {
    Write-Host "⚠ Could not create desktop shortcut: $_" -ForegroundColor Yellow
}
Write-Host ""

# Installation complete
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  Installation Complete!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Double-click 'Bil-dir' shortcut on your desktop" -ForegroundColor White
Write-Host "     OR run: .\start.bat" -ForegroundColor White
Write-Host ""
Write-Host "  2. Open your browser to: http://localhost:$Port" -ForegroundColor White
Write-Host ""
Write-Host "  3. Make sure you have provider CLIs installed:" -ForegroundColor White
Write-Host "     - Copilot CLI: npm install -g @githubnext/github-copilot-cli" -ForegroundColor Gray
Write-Host "     - Claude CLI: npm install -g @anthropic-ai/claude-code" -ForegroundColor Gray
Write-Host "     - Gemini CLI: npm install -g @google/generative-ai-cli" -ForegroundColor Gray
Write-Host ""
Write-Host "Press any key to start Bil-dir now, or Ctrl+C to exit..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

# Start the application
Write-Host ""
Write-Host "Starting Bil-dir..." -ForegroundColor Cyan
Start-Process -FilePath $startScript -WorkingDirectory $scriptDir
Write-Host "✓ Bil-dir started!" -ForegroundColor Green
Write-Host "Opening browser..." -ForegroundColor Cyan
Start-Sleep -Seconds 2
Start-Process "http://localhost:$Port"
