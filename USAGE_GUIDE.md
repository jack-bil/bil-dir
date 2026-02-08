# Usage Guide

## Setup
1. Create a virtual environment and install dependencies:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in keys if needed:
   ```powershell
   Copy-Item .env.example .env
   ```

## Run
```powershell
python app.py
```
Open the UI:
```
http://127.0.0.1:5025/
```

## Sessions
- Create a session from the left nav.
- Pick provider and optional working directory.
- Send messages; the app maintains provider session IDs internally.

## Master Console
- Open `/master` to see a consolidated feed of session responses.
- Route prompts to a session using:
  ```
  @@session-name: your message
  ```

## Tasks
- Create a task (manual or scheduled) in the left nav.
- Each task runs a single prompt on a provider.
- Use the task view to edit schedule, run now, or view output.

## Orchestrators
- Create an orchestrator with a goal and managed sessions.
- Orchestrators can inject next-step prompts into sessions.

## Health Dashboard
- Visit `/health/dashboard` for provider/MCP/task/session status.
- Use `/api/health/full` for JSON health data.

## Troubleshooting
- If a provider CLI is missing, set its path in `/config`.
- Ensure required keys are in `.env` (e.g., `BRAVE_API_KEY` for web search).
