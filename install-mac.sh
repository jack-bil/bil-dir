#!/bin/bash
# Bil-dir Installer for macOS
# This script installs Python (if needed) and sets up the Bil-dir orchestrator

set -e

PYTHON_VERSION="3.11.9"
PORT=5025
SKIP_PYTHON_INSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-python)
            SKIP_PYTHON_INSTALL=true
            shift
            ;;
        --python-version)
            PYTHON_VERSION="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=================================================="
echo "  Bil-dir Orchestrator - macOS Installer"
echo "=================================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to check Python installation
check_python() {
    if command -v python3 &> /dev/null; then
        VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        echo -e "${GREEN}✓ Python3 found: $VERSION${NC}"
        return 0
    elif command -v python &> /dev/null; then
        VERSION=$(python --version 2>&1 | awk '{print $2}')
        echo -e "${GREEN}✓ Python found: $VERSION${NC}"
        return 0
    fi
    return 1
}

# Function to check Homebrew
check_homebrew() {
    if command -v brew &> /dev/null; then
        echo -e "${GREEN}✓ Homebrew found${NC}"
        return 0
    fi
    return 1
}

# Function to install Homebrew
install_homebrew() {
    echo -e "${YELLOW}Homebrew not found. Installing Homebrew...${NC}"
    echo -e "${CYAN}(This may take a few minutes)${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for this session
    if [[ $(uname -m) == 'arm64' ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
}

# Function to install Python via Homebrew
install_python_homebrew() {
    echo -e "${YELLOW}Installing Python via Homebrew...${NC}"
    brew install python@3.11
    
    # Install tkinter (not included by default in Homebrew Python)
    echo -e "${YELLOW}Installing tkinter for GUI file picker...${NC}"
    brew install python-tk@3.11
    
    echo -e "${GREEN}✓ Python and tkinter installed${NC}"
}

# Function to install Python via official installer
install_python_official() {
    echo -e "${YELLOW}Downloading Python $PYTHON_VERSION installer...${NC}"
    
    # Determine architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" == "arm64" ]]; then
        PKG_URL="https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-macos11.pkg"
    else
        PKG_URL="https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-macosx10.9.pkg"
    fi
    
    PKG_PATH="/tmp/python-$PYTHON_VERSION.pkg"
    
    if curl -fsSL "$PKG_URL" -o "$PKG_PATH"; then
        echo -e "${GREEN}✓ Downloaded Python installer${NC}"
        echo -e "${YELLOW}Installing Python (requires sudo)...${NC}"
        sudo installer -pkg "$PKG_PATH" -target /
        rm "$PKG_PATH"
        echo -e "${GREEN}✓ Python installed${NC}"
    else
        echo -e "${RED}✗ Failed to download Python installer${NC}"
        echo -e "${YELLOW}Please install Python manually from: https://www.python.org/downloads/${NC}"
        exit 1
    fi
}

# Step 1: Check/Install Python
echo -e "${CYAN}[1/5] Checking Python installation...${NC}"
if check_python; then
    echo -e "${GREEN}Python is already installed.${NC}"
elif [ "$SKIP_PYTHON_INSTALL" = true ]; then
    echo -e "${RED}✗ Python not found and --skip-python specified.${NC}"
    exit 1
else
    # Ask user preference
    echo ""
    echo "Python not found. How would you like to install it?"
    echo "  1) Homebrew (recommended - easier updates)"
    echo "  2) Official Python installer"
    echo "  3) Skip (I'll install it manually)"
    read -p "Choose [1-3]: " choice
    
    case $choice in
        1)
            if ! check_homebrew; then
                install_homebrew
            fi
            install_python_homebrew
            ;;
        2)
            install_python_official
            ;;
        3)
            echo -e "${YELLOW}Please install Python manually and run this script again.${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            exit 1
            ;;
    esac
    
    # Verify installation
    if ! check_python; then
        echo -e "${RED}✗ Python installation verification failed.${NC}"
        echo -e "${YELLOW}Please restart your terminal and try again.${NC}"
        exit 1
    fi
fi
echo ""

# Determine Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

# Verify tkinter is available
echo -e "${CYAN}Checking tkinter (required for GUI folder picker)...${NC}"
if $PYTHON_CMD -c "import tkinter" 2>/dev/null; then
    echo -e "${GREEN}✓ tkinter is available${NC}"
else
    echo -e "${YELLOW}⚠ tkinter not available - folder picker will not work${NC}"
    echo -e "${CYAN}  To fix: brew install python-tk@3.11${NC}"
    echo -e "${CYAN}  Or manually type folder paths in the UI${NC}"
fi
echo ""

# Step 2: Install Python dependencies
echo -e "${CYAN}[2/5] Installing Python dependencies...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

if [ -f "$REQUIREMENTS_FILE" ]; then
    $PYTHON_CMD -m pip install --upgrade pip --quiet
    $PYTHON_CMD -m pip install -r "$REQUIREMENTS_FILE" --quiet
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠ requirements.txt not found, skipping...${NC}"
fi
echo ""

# Step 3: Create startup script
echo -e "${CYAN}[3/5] Creating startup script...${NC}"
START_SCRIPT="$SCRIPT_DIR/start.command"
cat > "$START_SCRIPT" << EOF
#!/bin/bash
cd "\$(dirname "\$0")"
echo "Starting Bil-dir Orchestrator..."
echo ""
echo "Server will be available at: http://localhost:$PORT"
echo "Press Ctrl+C to stop the server"
echo ""
$PYTHON_CMD app.py
EOF

chmod +x "$START_SCRIPT"
echo -e "${GREEN}✓ Created start.command${NC}"
echo ""

# Step 4: Create launch agent (optional)
echo -e "${CYAN}[4/5] Create launch agent (auto-start on login)?${NC}"
read -p "Install as launch agent? [y/N]: " create_agent

if [[ "$create_agent" =~ ^[Yy]$ ]]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/com.bildir.orchestrator.plist"
    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bildir.orchestrator</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_CMD</string>
        <string>$SCRIPT_DIR/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/server.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/error.log</string>
</dict>
</plist>
EOF
    
    # Load the launch agent
    launchctl load "$PLIST_PATH"
    echo -e "${GREEN}✓ Launch agent installed${NC}"
    echo -e "${CYAN}  Bil-dir will now start automatically on login${NC}"
    echo -e "${CYAN}  To stop: launchctl unload ~/Library/LaunchAgents/com.bildir.orchestrator.plist${NC}"
else
    echo -e "${YELLOW}⚠ Skipped launch agent creation${NC}"
fi
echo ""

# Step 5: Create Application alias
echo -e "${CYAN}[5/5] Creating Application alias...${NC}"
if [ -d "/Applications" ]; then
    ln -sf "$START_SCRIPT" "/Applications/Bil-dir.command" 2>/dev/null || {
        echo -e "${YELLOW}⚠ Could not create alias in /Applications (needs sudo)${NC}"
        echo -e "${CYAN}  Creating in ~/Applications instead...${NC}"
        mkdir -p "$HOME/Applications"
        ln -sf "$START_SCRIPT" "$HOME/Applications/Bil-dir.command"
    }
    echo -e "${GREEN}✓ Application alias created${NC}"
else
    echo -e "${YELLOW}⚠ Could not create application alias${NC}"
fi
echo ""

# Installation complete
echo "=================================================="
echo -e "${GREEN}  Installation Complete!${NC}"
echo "=================================================="
echo ""
echo -e "${CYAN}Next steps:${NC}"
echo -e "  1. Double-click ${GREEN}start.command${NC} in this folder"
echo -e "     OR open ${GREEN}Bil-dir${NC} from Applications"
echo ""
echo -e "  2. Open your browser to: ${GREEN}http://localhost:$PORT${NC}"
echo ""
echo -e "  3. Make sure you have provider CLIs installed:"
echo -e "     - Copilot CLI: ${CYAN}npm install -g @githubnext/github-copilot-cli${NC}"
echo -e "     - Claude CLI: ${CYAN}npm install -g @anthropic-ai/claude-code${NC}"
echo -e "     - Gemini CLI: ${CYAN}npm install -g @google/generative-ai-cli${NC}"
echo ""
read -p "Press Enter to start Bil-dir now, or Ctrl+C to exit..."

# Start the application
echo ""
echo -e "${CYAN}Starting Bil-dir...${NC}"
open "http://localhost:$PORT"
"$START_SCRIPT"
