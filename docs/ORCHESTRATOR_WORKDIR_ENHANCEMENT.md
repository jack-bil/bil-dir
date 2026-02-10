# Orchestrator Working Directory Enhancement - Implementation

**Date**: 2026-02-07
**Status**: ✅ Implemented

---

## Problem

Orchestrators only knew about **ONE working directory at a time** - whichever session just became idle. This prevented orchestrators from:
- Giving context-aware instructions to other managed sessions
- Referencing correct file paths across sessions
- Understanding project structure across microservices/multi-directory projects

**Previous behavior:**
```
Session working directory:
/Users/jack/myapp/backend
```
Only showed the session that just became idle, not all managed sessions.

---

## Solution

Modified `_run_orchestrator_decision()` to include **ALL managed session working directories** in the decision prompt.

**New behavior:**
```
Managed sessions and their working directories:
  - frontend: /Users/jack/myapp/client
  - backend: /Users/jack/myapp/server (just became idle)
  - docs: /Users/jack/myapp/documentation
```

Shows all managed sessions with their working directories, highlighting which just became idle.

---

## Code Changes

### File: `app.py`

**Function**: `_run_orchestrator_decision` (lines 416-455)

**Changes:**
1. **Removed**: Single session workdir lookup (line 422)
   ```python
   session_workdir = _get_session_workdir(session_name)
   ```

2. **Added**: Loop to build context for all managed sessions (lines 422-430)
   ```python
   # Build context for ALL managed sessions (not just the current one)
   session_contexts = []
   for managed_session_name in managed:
       managed_wd = _get_session_workdir(managed_session_name)
       is_current = " (just became idle)" if managed_session_name == session_name else ""
       session_contexts.append(f"  - {managed_session_name}: {managed_wd or '(default)'}{is_current}")

   session_context_text = "\n".join(session_contexts) if session_contexts else "  (none)"
   ```

3. **Updated**: Prompt template (lines 432-434)
   ```python
   # Before:
   Session working directory:
   {session_workdir or ""}

   # After:
   Managed sessions and their working directories:
   {session_context_text}
   ```

---

## Benefits

### 1. Better Cross-Session Coordination
Orchestrators can now give specific path references when coordinating work:
```
"Frontend team: Import the types from ../server/types.ts"
"Backend team: The frontend expects JSON in the format defined in /client/types/api.ts"
```

### 2. Smarter File References
Orchestrator knows:
- Frontend's package.json is in `/client/package.json`
- Backend's package.json is in `/server/package.json`
- Can give specific instructions about each

### 3. Better Task Distribution
```
"Frontend team (/client): Update UI components
 Backend team (/server): Add database migrations
 Docs team (/documentation): Document the new API"
```

### 4. Debugging Context
When something fails, orchestrator can say:
```
"Backend in /server failed to find config.json.
 Frontend in /client may have a similar issue.
 Check both locations."
```

---

## Testing

### Manual Test Steps

1. **Start the bil-dir Flask app**
   ```bash
   python app.py
   ```

2. **Create multiple sessions with different workdirs**
   - Via UI: Create sessions with explicit working directories
   - Or via API:
     ```bash
     curl -X POST http://localhost:5000/sessions \
       -H "Content-Type: application/json" \
       -d '{"name":"frontend", "workdir":"C:/app/client"}'

     curl -X POST http://localhost:5000/sessions \
       -H "Content-Type: application/json" \
       -d '{"name":"backend", "workdir":"C:/app/server"}'

     curl -X POST http://localhost:5000/sessions \
       -H "Content-Type: application/json" \
       -d '{"name":"docs", "workdir":"C:/app/documentation"}'
     ```

3. **Create an orchestrator managing these sessions**
   ```bash
   curl -X POST http://localhost:5000/orchestrators \
     -H "Content-Type: application/json" \
     -d '{
       "name":"App Builder",
       "provider":"codex",
       "managed_sessions":["frontend","backend","docs"],
       "goal":"Build a full-stack application",
       "enabled":true
     }'
   ```

4. **Send a message to one of the managed sessions**
   - This will make the session idle after response
   - Orchestrator will be triggered to make a decision

5. **Verify the prompt includes all workdirs**
   - Check the logs or orchestrator history
   - Look for the "Managed sessions and their working directories:" section
   - Should show all three sessions with their workdirs

### Automated Test

A test script is available at:
```
scratchpad/test_orchestrator_workdirs.py
```

**To run** (requires Flask app running on localhost:5000):
```bash
python scratchpad/test_orchestrator_workdirs.py
```

**What it tests:**
- Creates 4 sessions (3 with explicit workdirs, 1 with default)
- Creates orchestrator managing all sessions
- Verifies data structure is correct
- Confirms all session workdirs are accessible

---

## Edge Cases Handled

### 1. Session Without Workdir
If a session has no explicit workdir:
```
  - session_name: (default)
```

### 2. No Managed Sessions
If orchestrator has no managed sessions:
```
Managed sessions and their working directories:
  (none)
```

### 3. Session Workdir Changes
If a session's workdir changes after orchestrator creation:
- Orchestrator will see updated workdir on next decision
- ✅ Always shows current state

---

## Example Prompt

With the enhancement, orchestrators now receive prompts like:

```
TASK: Decide the next action for orchestrator "App Builder". Respond with ONLY valid JSON.

Manager instructions:
Act as the manager across any task type. Use the goal and context below to decide the next best action...

Goal:
Build a full-stack application with frontend, backend, and docs

Managed sessions and their working directories:
  - frontend: C:/app/client
  - backend: C:/app/server (just became idle)
  - docs: C:/app/documentation

This orchestrator ONLY manages these sessions: frontend, backend, docs.
Managed session just became idle: backend
Latest output:
[Backend output here...]

Recent conversation (last 5 messages, if any):
None

Respond with one of:
{"action":"inject_prompt","target_session":"<name>","prompt":"..."}
{"action":"wait"}
{"action":"ask_human","question":"..."}
```

**Notice**: The orchestrator now sees ALL three working directories, enabling it to give specific, context-aware instructions to any managed session.

---

## Implementation Type

This is a **code-based enhancement** (automatic), not a **template-based enhancement** (user-configurable).

- Session context is built dynamically in `_run_orchestrator_decision()`
- No template variables needed in config (like `{session_working_directories}`)
- Works automatically for all orchestrators
- No user configuration required

The base_prompt in the config console remains a simple instruction template like "Act as the manager..." and doesn't need template variable support.

---

## Related Documentation

- **Problem Analysis**: `docs/ORCHESTRATOR_WORKDIR_ISSUE.md`
- **Orchestrator Manager**: `core/orchestrator_manager.py`
- **Config Utilities**: `utils/config.py`

---

## Impact

**Priority**: HIGH ⚠️
**Effort**: LOW ✅ (~10 lines of code)
**Impact**: HIGH 🚀

Dramatically improves orchestrator's ability to:
- Coordinate across microservices
- Reference correct file paths
- Understand project structure
- Give specific, actionable instructions

---

## Future Enhancements

Potential improvements to consider:

1. **Worker Kickoff Enhancement**: Also show all session workdirs when workers are initialized (currently workers only see their own workdir)

2. **Relative Path Helpers**: For very long paths, could show relative paths from a common root

3. **Workdir Change Notifications**: Notify orchestrator when a managed session changes working directory

4. **Cross-Session File References**: Helper to generate relative paths between session workdirs (e.g., `../server/types.ts` from client perspective)
