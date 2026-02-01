# Copilot Instructions for Bil-dir

Bil-dir is a Flask-based orchestration server that runs multiple AI CLI tools (Codex, GitHub Copilot, Gemini, Claude) with session management, scheduled tasks, and MCP (Model Context Protocol) server support.

## Running the Application

### Development
```powershell
python app.py
# Opens on http://127.0.0.1:5025/chat
```

### Production (Windows)
```powershell
.\scripts\bildir.ps1 -Port 5025
# Uses Waitress for SSE and concurrency
```

### First Run
The app auto-creates these files in the project directory (all git-ignored):
- `sessions.json` - named session storage
- `tasks.json` - scheduled task definitions
- `history.json` - conversation history (root fallback)
- `log.jsonl` - event logs
- `client_config.json` - UI preferences & provider settings
- `context/` - cross-provider context briefings
- `providers/config.toml` - Codex MCP configuration

## Architecture

### Core Components

**Flask App (`app.py`)** - Single 2900+ line file containing:
- Flask routes for `/chat`, `/tasks`, `/config`, `/stream`, `/exec`
- CLI execution functions for each provider
- Session management with per-provider session IDs
- Task scheduler thread (background daemon)
- SSE (Server-Sent Events) for live streaming
- MCP configuration writers (JSON for Copilot, TOML for Codex)

**Templates (`templates/`)** - HTML with embedded JavaScript:
- `chat.html` - Main chat UI with session list, SSE updates, provider switcher
- `config.html` - Provider paths, permissions, MCP server management
- `diag.html` - Diagnostic page showing provider/model status
- `index.html` - Landing page with route list
- `result.html` - Deprecated (use `/exec` JSON endpoint)

### Data Flow

1. **Session Management**
   - Named sessions (user-defined strings like "analysis-a")
   - Each session tracks multiple provider-specific session IDs
   - Sessions stored in `sessions.json` with structure:
     ```json
     {
       "session-name": {
         "provider": "codex",
         "session_ids": {
           "codex": "codex-abc123",
           "copilot": "copilot-def456"
         },
         "workdir": "/path/to/project"
       }
     }
     ```

2. **Cross-Provider Context**
   - When switching providers (e.g., Codex → Copilot), system generates summary
   - Summary saved to `context/{session_name}_context.md`
   - Context injected into new provider's first prompt (not on resume)
   - See `_generate_session_summary()`, `_append_context_briefing()`, `_load_context_briefing_text()`

3. **Conversation History**
   - Stored per-session in `history.json` (root) or `.codex_history.json` (workdir)
   - Structure: `{session_id: {messages: [...], tool_outputs: [...]}}`
   - Used for cross-provider summaries

4. **Task Execution**
   - Background scheduler thread runs every 30 seconds
   - Task types: `interval`, `daily`, `weekly`, `once`, `manual`
   - Each task runs as async job (same as streaming)
   - Updates `next_run` timestamp after execution

### Provider Execution Patterns

Each provider has two execution modes:
1. **Blocking** (`_run_{provider}_exec`) - Used by `/exec` endpoint
2. **Streaming** (`_start_{provider}_job`) - Used by `/stream` endpoint and tasks

**Important**: Context injection only happens when:
- NOT resuming a session (`resume_session_id` is None)
- NOT continuing last session (`resume_last` is False)
- Context briefing exists for the session

## Key Conventions

### Thread Safety
- `_SESSION_LOCK` - Protects sessions.json reads/writes
- `_TASK_LOCK` - Protects tasks.json reads/writes
- `_JOB_LOCK` - Protects in-memory `_JOBS` dict (streaming)
- Always use context managers: `with _SESSION_LOCK:`

### Session Status Broadcasting
- `_broadcast_sessions_snapshot()` - Notifies SSE clients of session changes
- `_broadcast_tasks_snapshot()` - Notifies SSE clients of task changes
- Called after any session/task modification

### Provider Resolution
- Default provider: `codex`
- Supported: `codex`, `copilot`, `gemini`, `claude`
- User can request specific provider or use session's current provider
- Session provider changes trigger context summary generation

### MCP Configuration
- Codex: Writes TOML to `providers/config.toml`
- Copilot: Writes JSON to `mcp.json` and exports `MCP_SERVERS_PATH` env var
- Both read from `client_config.json["mcp_json"]` field
- Config accepts `mcpServers` or `servers` key (normalized to `mcpServers`)

### Working Directory Handling
- Global default: `DEFAULT_CWD` (env `BILDIR_CWD` or current directory)
- Configurable per-request: `body.get("cwd")`
- Session-specific: `sessions[name]["workdir"]`
- Codex always uses `--skip-git-repo-check` flag

### Legacy File Migration
- On startup, migrates `.codex_*` prefixed files to new names
- Example: `.codex_sessions.json` → `sessions.json`
- Preserves backward compatibility

## Testing

No formal test suite. Manual testing scripts:
- `test_*.py` - Playwright-based UI tests (require running server)
- `scripts/ui_playwright_check.py` - Session status verification

To test manually:
```python
import requests
BASE = "http://127.0.0.1:5025"

# Create session
r = requests.post(f"{BASE}/exec", json={
    "prompt": "List files",
    "provider": "codex",
    "session_name": "test-session"
})
print(r.json())

# Resume session
r = requests.post(f"{BASE}/exec", json={
    "prompt": "Summarize the repo",
    "session_name": "test-session"
})
```

## Common Modifications

### Adding a New Provider
1. Add to `SUPPORTED_PROVIDERS` set
2. Create `_resolve_{provider}_path(config)` function
3. Create `_run_{provider}_exec()` function (blocking mode)
4. Create `_start_{provider}_job()` function (streaming mode)
5. Add case to `_start_job()` dispatcher
6. Update `_provider_path_status()` dict
7. Add UI option to provider dropdown in `chat.html`

### Adding a New Task Schedule Type
1. Update `_normalize_task()` to accept new schedule structure
2. Add schedule logic to `_compute_next_run()`
3. Add UI display to `_schedule_summary()`
4. Update task form in `chat.html` with new schedule fields

### Modifying SSE Behavior
- SSE queues: `_SESSION_SUBSCRIBERS` (session updates), `_TASK_SUBSCRIBERS` (task updates)
- Job output: Each job broadcasts via `job.broadcast(f"data: {text}\n\n")`
- Format: `event: {type}\ndata: {payload}\n\n` (double newline required)

## File Locations (Environment Variables)

Override with env vars for custom locations:
- `BILDIR_CWD` - Working directory (default: current directory)
- `BILDIR_SESSION_STORE` - Sessions JSON path
- `BILDIR_TASK_STORE` - Tasks JSON path
- `BILDIR_HISTORY_STORE` - History JSON path
- `BILDIR_CLIENT_CONFIG` - Client config JSON path
- `BILDIR_LOG_STORE` - Event log path
- `MCP_JSON_PATH` - MCP servers JSON path
- `BILDIR_PROVIDER_CONFIG` - Codex TOML config path
- `CODEX_PATH` - Override codex CLI path

## Debugging

Enable debug logging by checking `context_debug.log`:
```python
logger.info(f"[Context] Message here")  # Info level
logger.debug(f"[Context] Detail here")  # Debug level (verbose)
```

Common issues:
- **SSE not streaming**: Check Waitress vs Flask dev server (dev server may buffer)
- **Context not injecting**: Verify `resume_session_id` and `resume_last` are both falsy
- **Task not running**: Check `enabled=True`, `next_run` timestamp, and scheduler logs
- **Provider not found**: Check CLI is in PATH or set custom path in `/config`
