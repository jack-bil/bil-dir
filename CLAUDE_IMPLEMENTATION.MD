# CLAUDE_IMPLEMENTATION

This document describes how Claude sessions are created, resumed, and stored in bil-dir.

## Summary

- Claude sessions start with **no synthetic session ID**.
- After the first Claude response, we discover the **real Claude UUID** by scanning Claude’s temp storage.
- We only resume when we have a **real UUID**.
- Prompts are sent via **stdin** to support multiline input.

## Session ID Lifecycle

1. **Session creation**
   - New Claude sessions start with `session_id = None`.
   - We do **not** generate fake IDs for Claude.

2. **First run**
   - Claude runs with stdin input:
     - `claude --dangerously-skip-permissions` (stdin prompt)
   - We record a `start_time` before the run.

3. **UUID discovery**
   - After the run, we scan Claude’s temp folder for a UUID created **after** `start_time`:
     - `C:\Users\<user>\AppData\Local\Temp\claude\<encoded-workdir>\<uuid>`
   - Workdir is encoded by replacing path separators and `:` with `-` (Claude’s format).
   - We only search the **exact workdir path** and only accept a UUID whose folder mtime is newer than the run start.

4. **Assignment**
   - The discovered UUID is stored in `sessions.json` for that session name/provider.

5. **Resume**
   - Resume only happens if the stored `session_id` is a **real UUID**.
   - Resume command:
     - `claude --dangerously-skip-permissions --resume <uuid>`

## Commands Used

- **New Claude run (multiline):**
  - `claude --dangerously-skip-permissions` (prompt via stdin)

- **Resume Claude run:**
  - `claude --dangerously-skip-permissions --resume <uuid>`

- **Auto-init (if enabled):**
  - Calls `_run_claude_exec("/init", ...)` which uses stdin as above

## Why This Design

- Claude’s CLI creates its own session UUIDs; we must capture them after the run.
- In reused workdirs, many old UUIDs exist. We avoid attaching the wrong one by:
  - only accepting UUIDs created after the run start time
  - restricting discovery to the exact workdir

## Known Limitations

- If Claude delays writing the UUID folder, we may not capture it immediately.
  - The code retries briefly; if no UUID appears, it will try again on the next prompt.
- If temp folders are deleted, the UUID cannot be discovered.

## Files Involved

- `app.py`
  - `_run_claude_exec`
  - `_start_claude_job`
  - `_get_latest_claude_session_id`
  - `_wait_for_claude_session_id`
