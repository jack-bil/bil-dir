# Bil-dir Installation Guide (Windows)

## Quick Install

**Option 1: Double-click installer (Recommended)**
1. Right-click `install.bat` → **Run as Administrator**
2. Follow the prompts
3. Done! A desktop shortcut will be created

**Option 2: PowerShell (Advanced)**
```powershell
# Run as Administrator
PowerShell -ExecutionPolicy Bypass -File install.ps1
```

## What the Installer Does

1. ✅ Checks if Python is installed
2. ✅ Downloads and installs Python 3.11.9 silently (if needed)
3. ✅ Installs dependencies from `requirements.txt`
4. ✅ Creates `start.bat` script
5. ✅ Creates desktop shortcut

## System Requirements

- **OS**: Windows 10 or later
- **RAM**: 2GB minimum
- **Disk**: 500MB free space
- **Admin Rights**: Required for Python installation

## Python Silent Install Details

The installer uses these Python flags:
```
/quiet              - No UI during install
InstallAllUsers=1   - Install for all users
PrependPath=1       - Add Python to PATH
Include_pip=1       - Include pip package manager
```

## Manual Python Install (if automatic fails)

1. Download Python from: https://www.python.org/downloads/
2. Run installer with "Add to PATH" checked
3. Restart PowerShell/CMD
4. Run `install.bat` again with `-SkipPythonInstall`

## Provider CLI Setup

After installation, install the AI provider CLIs:

**GitHub Copilot:**
```bash
npm install -g @githubnext/github-copilot-cli
```

**Claude:**
```bash
npm install -g @anthropic-ai/claude-code
```

**Gemini:**
```bash
npm install -g @google/generative-ai-cli
```

## Running Bil-dir

**Method 1:** Double-click desktop shortcut `Bil-dir`

**Method 2:** Run `start.bat` in this folder

**Method 3:** Manual start
```cmd
cd path\to\orch
python app.py
```

Then open: http://localhost:5025

## Troubleshooting

**"Python not found" after install**
- Restart PowerShell/CMD to refresh PATH
- Log out and log back in
- Reboot computer

**"Access Denied" during Python install**
- Run `install.bat` as Administrator
- Disable antivirus temporarily
- Check Windows User Account Control settings

**Dependencies fail to install**
- Check internet connection
- Update pip: `python -m pip install --upgrade pip`
- Install manually: `pip install -r requirements.txt`

**Port 5025 already in use**
- Edit `app.py` and change `PORT = 5025` to another port
- Or edit `install.ps1` parameter: `-Port 5026`

## Uninstalling

1. Delete desktop shortcut
2. Delete this folder
3. (Optional) Uninstall Python from Windows Settings → Apps

## Advanced Options

**Custom Python version:**
```powershell
.\install.ps1 -PythonVersion "3.12.0"
```

**Skip Python install:**
```powershell
.\install.ps1 -SkipPythonInstall
```

**Custom port:**
```powershell
.\install.ps1 -Port 8080
```

---

# Bil-dir Installation Guide (macOS)

## Quick Install

**Option 1: Run installer script**
```bash
chmod +x install-mac.sh
./install-mac.sh
```

**Option 2: One-liner**
```bash
curl -fsSL https://raw.githubusercontent.com/yourusername/bil-dir/main/install-mac.sh | bash
```

## What the Installer Does

1. ✅ Checks if Python is installed
2. ✅ Offers Homebrew or official Python installer
3. ✅ Installs dependencies from `requirements.txt`
4. ✅ Creates `start.command` launcher
5. ✅ (Optional) Creates launch agent for auto-start
6. ✅ Creates alias in Applications folder

## System Requirements

- **OS**: macOS 10.15 (Catalina) or later
- **RAM**: 2GB minimum
- **Disk**: 500MB free space
- **Xcode Command Line Tools**: Recommended

## Python Installation Options

The installer offers two methods:

**Method 1: Homebrew (Recommended)**
- Easier to update and maintain
- Installs via `brew install python@3.11`
- If Homebrew not installed, installer will install it

**Method 2: Official Python Installer**
- Downloads from python.org
- Automatically detects Apple Silicon (M1/M2) vs Intel
- Installs system-wide

## First-Time Setup

**Install Xcode Command Line Tools (if needed):**
```bash
xcode-select --install
```

**Make installer executable:**
```bash
chmod +x install-mac.sh
```

## Provider CLI Setup

After installation, install the AI provider CLIs:

**GitHub Copilot:**
```bash
npm install -g @githubnext/github-copilot-cli
```

**Claude:**
```bash
npm install -g @anthropic-ai/claude-code
```

**Gemini:**
```bash
npm install -g @google/generative-ai-cli
```

## Running Bil-dir

**Method 1:** Double-click `start.command` in this folder

**Method 2:** Open from Applications folder (alias created during install)

**Method 3:** Run from terminal
```bash
cd path/to/orch
python3 app.py
```

Then open: http://localhost:5025

## Launch Agent (Auto-Start)

If you chose to install the launch agent during setup:

**Check status:**
```bash
launchctl list | grep bildir
```

**Stop service:**
```bash
launchctl unload ~/Library/LaunchAgents/com.bildir.orchestrator.plist
```

**Start service:**
```bash
launchctl load ~/Library/LaunchAgents/com.bildir.orchestrator.plist
```

**Remove auto-start:**
```bash
launchctl unload ~/Library/LaunchAgents/com.bildir.orchestrator.plist
rm ~/Library/LaunchAgents/com.bildir.orchestrator.plist
```

## Troubleshooting

**"Permission denied" when running installer**
```bash
chmod +x install-mac.sh
```

**"tkinter not available" - Folder picker doesn't work**

If you installed Python via Homebrew:
```bash
brew install python-tk@3.11
```

If you installed via official installer, tkinter should already be included. If not:
- Reinstall Python from python.org
- Or manually type/paste folder paths in the UI (folder picker will show error but app works)

**"python3: command not found" after install**
- Close and reopen Terminal
- Check PATH: `echo $PATH`
- Verify Python: `which python3`

**Homebrew installation stuck**
- Press Enter when prompted
- May need to enter your password
- Takes 5-10 minutes on first install

**"Operation not permitted" errors**
- macOS Gatekeeper/Security settings
- System Preferences → Security & Privacy → Full Disk Access
- Add Terminal.app

**Dependencies fail to install**
- Update pip: `python3 -m pip install --upgrade pip`
- Install manually: `pip3 install -r requirements.txt`

**Port 5025 already in use**
```bash
./install-mac.sh --port 8080
```

## Uninstalling

**Remove application:**
```bash
# Stop launch agent (if installed)
launchctl unload ~/Library/LaunchAgents/com.bildir.orchestrator.plist
rm ~/Library/LaunchAgents/com.bildir.orchestrator.plist

# Remove application alias
rm /Applications/Bil-dir.command
# or
rm ~/Applications/Bil-dir.command

# Delete folder
cd ..
rm -rf orch
```

**Uninstall Python (Homebrew):**
```bash
brew uninstall python@3.11
```

**Uninstall Python (Official):**
- Use official uninstaller or delete manually from `/Library/Frameworks/Python.framework/`

## Advanced Options

**Skip Python install:**
```bash
./install-mac.sh --skip-python
```

**Custom Python version:**
```bash
./install-mac.sh --python-version "3.12.0"
```

**Custom port:**
```bash
./install-mac.sh --port 8080
```

## Security Notes

- The installer downloads Python from python.org (official source)
- Packages installed from PyPI (official Python package index)
- Launch agent runs only when you're logged in
- No system files are modified (except optional Python install)

## Support

For issues, check:
- Python version: `python3 --version` (should be 3.9+)
- Pip installed: `pip3 --version`
- Dependencies: `pip3 list`
- Logs: Check `server.log` and `error.log` in app folder
