# Bil-dir

Bil-dir is a local Flask app that orchestrates multiple AI CLIs (Codex, Copilot, Gemini, Claude) with sessions, tasks, orchestrators, and MCP support.

## What it does
- Chat UI with named sessions and per-session model selection.
- Master console that aggregates replies from all sessions and supports routing via `@@session-name: message`.
- Orchestrators: manager agents that supervise one or more sessions with a goal and history.
- Tasks: one-shot prompts that can be scheduled (interval/daily/weekly/once) and run independently.
- CLI-style streaming output with ANSI color support and activity panes.
- Server-Sent Events (SSE) for live updates.

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Create a local env file:
```powershell
Copy-Item .env.example .env
```
Then edit `.env` with your keys (e.g., `BRAVE_API_KEY`, `GEMINI_API_KEY`).

**MCP Server Setup (Automatic):**
On first run, bil-dir will automatically:
- Install MCP server dependencies (`npm install` in `mcp_servers/`)
- Configure the `bildir-tasks` MCP server for all providers
- No manual configuration needed!

If you don't have Node.js installed, get it from [nodejs.org](https://nodejs.org/) (required for MCP servers).

### First Run
When you first run the app, it will automatically create these files in the project directory as needed:
- `sessions.json` - Session storage
- `tasks.json` - Task storage
- `orchestrators.json` - Orchestrator storage
- `history.json` - Command history
- `log.jsonl` - Event logs
- `client_config.json` - UI preferences (includes MCP config)
- `mcp.json` - MCP server configuration
- `providers/config.toml` - Provider-specific MCP config
- `context/` - Context files directory

These files are git-ignored and will be created with default values on first startup.

## Prereqs
- Python 3.10+ (3.11/3.12 ok)
- Any CLIs you plan to use installed and on PATH (see below)
- Node.js + npm if you want to use MCP servers installed via `npx`

## Run (dev)
```powershell
python app.py
```
Open in your browser:
```
http://127.0.0.1:5025/
```

## Production (Windows)
Use Waitress to handle SSE and concurrent requests safely.
```powershell
pip install -r requirements.txt
.\scripts\bildir.ps1 -Port 5025
```

## CLIs required
Install the CLIs you want to use and make sure they are on PATH (or set the CLI path in `/config`):
- Codex: `codex`
- GitHub Copilot: `copilot`
- Gemini: `gemini`
- Claude: `claude`

Gemini and Claude run locally through their CLIs (no HTTP API calls from the app).

## Config
Open `/config` and use the tabs:
- **General**: CLI availability + CLI paths. Set a default working directory.
- **Permissions**: Per-model full-permissions toggles and sandbox mode.
- **Orchestrator**: Base manager prompt and reset to defaults.
- **MCP**: Configure MCP servers for all providers. Add any MCP server (brave-search, gmail, custom servers, etc.)

### MCP Configuration Management
When you add/edit MCP servers in the MCP tab, bil-dir automatically updates:
  - `providers/config.toml` (Codex - local)
  - `mcp.json` (Copilot/Claude - local)
  - `.gemini/settings.json` (Gemini - project-level)
  - `~/.codex/config.toml` (Codex - global)
  - `~/.copilot/mcp-config.json` (Copilot - global)
  - `~/.claude.json` (Claude - global)

This means MCP servers work whether you use providers through bil-dir OR run CLIs directly from command line.

Enable MCP for Copilot by adding this to `client_config.json`:
```json
{
  "copilot_enable_mcp": true
}
```

## MCP (Model Context Protocol)

bil-dir has **two MCP-related components**:

### 1. MCP Configuration System
bil-dir manages MCP server configurations for all AI providers. Configure ANY MCP servers via:
- Web UI: `/config` → MCP tab
- File: `client_config.json` → `mcp_json` field

When you add/update MCP servers, bil-dir automatically syncs the configuration to:
- Providers when used through bil-dir
- Global CLI configs for standalone use (`~/.codex/config.toml`, `~/.copilot/mcp-config.json`, `~/.claude.json`)

**Examples of MCP servers you can add:**
- `brave-search` - Web search capabilities
- `gmail` - Email operations
- `github` - GitHub operations
- `bildir-tasks` - Task scheduling (built-in, see below)
- Any custom MCP server

### 2. Built-in MCP Server: `bildir-tasks`
bil-dir includes a task scheduler MCP server that allows AI providers to:
- Schedule tasks (interval/daily/weekly/once)
- List and manage existing tasks
- Enable/disable tasks
- View task history

**Example:** Ask any provider: *"Create a task to check the weather every 30 minutes"*

**Automatic Setup:**
On first run, bil-dir automatically:
- Installs `bildir-tasks` dependencies
- Adds `bildir-tasks` to MCP configuration
- Syncs to all provider configs (both bil-dir and standalone CLI)

| Provider | Through bil-dir | Standalone CLI | Config Location |
|----------|----------------|----------------|-----------------|
| **Codex** | ✅ | ✅ | `~/.codex/config.toml` |
| **Copilot** | ✅ | ✅ | `~/.copilot/mcp-config.json` |
| **Claude** | ✅ | ✅ | `~/.claude.json` |
| **Gemini** | ✅ | ✅ | `.gemini/settings.json` (project-level) |

## Usage
Health check:
```powershell
Invoke-RestMethod http://127.0.0.1:5025/health
```

Run a Codex prompt (returns `session_id` when available):
```powershell
$body = @{ prompt = "List files in the repo." } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/exec -Body $body -ContentType "application/json"
```

Resume a session (client-managed `session_id`):
```powershell
$body = @{ prompt = "Continue with more details."; session_id = "SESSION_ID" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/exec -Body $body -ContentType "application/json"
```

Resume a named session (server-managed name -> session_id):
```powershell
$body = @{ prompt = "Continue with more details."; session_name = "analysis-a" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/exec -Body $body -ContentType "application/json"
```

Resume last session:
```powershell
$body = @{ prompt = "Continue."; resume_last = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/exec -Body $body -ContentType "application/json"
```

Stream output (Server-Sent Events):
```powershell
$body = @{ prompt = "Summarize the repo." } | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:5025/stream -Body $body -ContentType "application/json"
```

List named sessions:
```powershell
Invoke-RestMethod http://127.0.0.1:5025/sessions
```

Delete a named session:
```powershell
Invoke-RestMethod -Method Delete -Uri http://127.0.0.1:5025/sessions/analysis-a
```

## Tasks (API)
Create a task:
```powershell
$body = @{
  name = "weather"
  prompt = "Get the weather for ZIP 19128 and append it to C:\\Users\\jackb\\weather.csv"
  provider = "codex"
  schedule = @{ type = "interval"; minutes = 5 }
  enabled = $true
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/tasks -Body $body -ContentType "application/json"
```

Run a task once:
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5025/tasks/{task_id}/run
```

Update a task:
```powershell
$body = @{ enabled = $false } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Uri http://127.0.0.1:5025/tasks/{task_id} -Body $body -ContentType "application/json"
```

## Master console
Open `/master` to see a consolidated feed of session responses. You can route messages to a specific session from the master input using:
```
@@session-name: your message here
```

## Recent enhancements
- Master console keeps the latest message at the bottom and preserves per-session color prefixes.
- Inline markdown now renders in master console lines (without changing the terminal look).
- Session menu now opens directly under the menu button and clamps to the viewport.
- Task flyout menu opens vertically and closes on scroll/resize, matching session menu behavior.
- Master prompt input no longer triggers spellcheck underlines for `@@session:`.

## Orchestrators
Create orchestrators from the UI to manage sessions with a goal. Orchestrators keep a decision history and can inject prompts into managed sessions.

## Security notes
- Bind to localhost unless you add authentication.
