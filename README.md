# Bil-dir

Bil-dir is a local Flask app that orchestrates multiple AI CLIs (Codex, Copilot, Gemini, Claude) with sessions, tasks, and MCP support.

## What it does
- Chat UI with named sessions and per-session model selection.
- Tasks: one-shot prompts that can be scheduled (interval/daily/weekly/once) and run independently.
- MCP configuration for Codex + Copilot.
- Server-Sent Events (SSE) for live updates.

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

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
http://127.0.0.1:5025/chat
```

## Production (Windows)
Use Waitress to handle SSE and concurrent requests safely.
```powershell
pip install -r requirements.txt
.\scripts\run_waitress.ps1 -Port 5025
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
- General: CLI availability + CLI paths.
- Set a default working directory (used when no per-request path is set).
- Permissions: per-model full-permissions toggles and sandbox mode (Codex honors `--sandbox` from here).
- MCP: JSON config + quick-add buttons. Applies to Codex and Copilot.

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

## Security notes
- Bind to localhost unless you add authentication.
