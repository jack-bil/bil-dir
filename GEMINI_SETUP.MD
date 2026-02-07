# Gemini Setup (Local Notes)

This file captures configuration notes and troubleshooting steps for Gemini CLI integration in bil-dir. Do not commit if it contains secrets.

## Auth requirements
- Gemini CLI requires authentication via one of:
  - `GEMINI_API_KEY` environment variable
  - Google Cloud auth (GCA / Vertex AI)
- The CLI will error if neither is present:
  "Please set an Auth method in ...\.gemini\settings.json or specify ... GEMINI_API_KEY ..."

## Where the key can live
- The CLI itself does NOT reliably read auth from `~/.gemini/settings.json` unless the process environment includes the key.
- For bil-dir, we inject `GEMINI_API_KEY` into the Gemini subprocess using values read from:
  1) `<cwd>/.gemini/settings.json`
  2) `~/.gemini/settings.json`
- This allows sessions and tasks to run without a global environment variable, as long as a settings file exists and contains `auth.api_key`.

## Required settings.json structure
Example `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": { "BRAVE_API_KEY": "${BRAVE_API_KEY}" }
    }
  },
  "auth": {
    "type": "api_key",
    "api_key": "YOUR_KEY"
  }
}
```

## Important implementation details in app.py
- `_run_gemini_exec(...)` (tasks + non-stream exec) injects `GEMINI_API_KEY` into subprocess env if missing.
- `_start_gemini_job(...)` (streaming sessions) must also inject the key.
- `_get_gemini_api_key_from_settings(cwd)` reads the key from project or user settings.
- `_resolve_gemini_path(...)` must find `gemini.cmd` on Windows if PATH is missing:
  - `%APPDATA%\npm\gemini.cmd`
  - `%USERPROFILE%\AppData\Roaming\npm\gemini.cmd`
  - `%ProgramFiles%\nodejs\gemini.cmd`

## Troubleshooting checklist
1. Confirm `~/.gemini/settings.json` exists and has `auth.api_key`.
2. Confirm server was restarted after code changes.
3. Verify `gemini.cmd` is discoverable:
   - `where gemini` (may be empty)
   - Check `%APPDATA%\npm\gemini.cmd`
4. Repro with direct stream call:
   - POST `/stream` with provider `gemini` and a simple prompt.
5. If stream still errors, check that `_start_gemini_job` is passing env.

## Known failure mode
- Streaming sessions failed while tasks worked because `_start_gemini_job` was not injecting `GEMINI_API_KEY` into the subprocess env.
- Fix: add the same env injection used by `_run_gemini_exec`.

## Security note
- Do not commit real API keys to git.
- Keep this file local if it ever includes secrets.
