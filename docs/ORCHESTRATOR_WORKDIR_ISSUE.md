# Orchestrator Working Directory Context Issue

## Problem

The orchestrator only knows about **ONE working directory at a time** - whichever session just became idle. It doesn't have visibility into ALL managed sessions and their working directories.

---

## Current Behavior

### When Orchestrator Makes Decision (app.py lines 416-446)

**Prompt sent to orchestrator:**
```
TASK: Decide the next action...

Goal:
Build a full-stack app

Session working directory:
/Users/jack/myapp/backend    ← Only shows the session that just became idle

This orchestrator ONLY manages these sessions: frontend, backend, docs
Managed session just became idle: backend
Latest output:
[backend output here...]
```

**Problem:**
- Orchestrator knows `backend` just finished
- Orchestrator knows it manages `frontend`, `backend`, `docs`
- **BUT orchestrator doesn't know WHERE frontend and docs are working!**

---

## Why This Matters

### Scenario:
```
Orchestrator goal: "Build a full-stack application"
Managed sessions:
  - frontend: /Users/jack/myapp/client
  - backend:  /Users/jack/myapp/server
  - docs:     /Users/jack/myapp/documentation
```

### What Happens:

1. **Backend finishes** implementing API endpoints
2. Orchestrator gets decision prompt with:
   - Session: `backend`
   - Workdir: `/Users/jack/myapp/server`
   - ✅ Knows backend is in `/server`
   - ❌ Doesn't know frontend is in `/client`
   - ❌ Doesn't know docs is in `/documentation`

3. Orchestrator might say:
   ```json
   {
     "action": "inject_prompt",
     "target_session": "frontend",
     "prompt": "Update the frontend to call the new API endpoints at ./api/users.js"
   }
   ```

4. **Frontend session** receives prompt but:
   - It's working in `/Users/jack/myapp/client`
   - The path `./api/users.js` doesn't make sense without context
   - Frontend doesn't know backend is in `../server`

### What SHOULD Happen:

If orchestrator knew all workdirs:
```json
{
  "action": "inject_prompt",
  "target_session": "frontend",
  "prompt": "The backend API is ready. Update frontend to call endpoints. Backend is in /Users/jack/myapp/server and exposes /api/users endpoint. Frontend should fetch from http://localhost:3000/api/users"
}
```

Much more specific and actionable!

---

## Current Code

### Orchestrator Decision Prompt (app.py lines 422-433)
```python
session_workdir = _get_session_workdir(session_name)  # Only current session
prompt = f"""...
Session working directory:
{session_workdir or ""}

This orchestrator ONLY manages these sessions: {", ".join(managed)}
Managed session just became idle: {session_name}
...
"""
```

### Worker Kickoff Prompt (core/orchestrator_manager.py lines 142-150)
```python
def _build_worker_kickoff_prompt(goal, role, template=None, workdir=None):
    return (
        f"Project goal:\n{goal}\n"
        f"Session working directory:\n{workdir or ''}\n\n"  # Only this session
        ...
    )
```

---

## Proposed Solution

### Add Session Context to Orchestrator Prompts

**Collect all managed session workdirs:**
```python
# In _run_orchestrator_decision:
session_contexts = []
for managed_session in managed:
    session_wd = _get_session_workdir(managed_session)
    session_contexts.append(f"  - {managed_session}: {session_wd or '(no workdir)'}")
session_context_text = "\n".join(session_contexts)
```

**Include in prompt:**
```python
prompt = f"""TASK: Decide the next action...

Goal:
{goal}

Managed sessions and working directories:
{session_context_text}

Managed session just became idle: {session_name}
Latest output from {session_name}:
{latest_output}
...
"""
```

**Example output:**
```
Managed sessions and working directories:
  - frontend: /Users/jack/myapp/client
  - backend: /Users/jack/myapp/server
  - docs: /Users/jack/myapp/documentation

Managed session just became idle: backend
```

---

## Benefits

### 1. **Better Cross-Session Coordination**
Orchestrator can reference specific paths when coordinating work:
```
"Frontend team: Import the types from ../server/types.ts"
"Backend team: The frontend expects JSON in the format defined in /client/types/api.ts"
```

### 2. **Smarter File References**
Orchestrator knows:
- Frontend's package.json is in `/client/package.json`
- Backend's package.json is in `/server/package.json`
- Can give specific instructions about each

### 3. **Better Task Distribution**
```
"Frontend team (/client): Update UI components
 Backend team (/server): Add database migrations
 Docs team (/documentation): Document the new API"
```

### 4. **Debugging Context**
When something fails, orchestrator can say:
```
"Backend in /server failed to find config.json.
 Frontend in /client may have a similar issue.
 Check both locations."
```

---

## Implementation Changes Needed

### File: `app.py`

**Function: `_run_orchestrator_decision` (lines 416-446)**

**Current:**
```python
session_workdir = _get_session_workdir(session_name)
prompt = f"""...
Session working directory:
{session_workdir or ""}
...
"""
```

**Proposed:**
```python
# Build context for ALL managed sessions
session_contexts = []
for managed_session_name in managed:
    managed_wd = _get_session_workdir(managed_session_name)
    is_current = " (just became idle)" if managed_session_name == session_name else ""
    session_contexts.append(f"  - {managed_session_name}: {managed_wd or '(default)'}{is_current}")

session_context_text = "\n".join(session_contexts) if session_contexts else "  (none)"

prompt = f"""TASK: Decide the next action for orchestrator "{orch.get('name')}".

Manager instructions:
{base_prompt}

Goal:
{goal}

Managed sessions and their working directories:
{session_context_text}

Session that just became idle: {session_name}
Latest output from {session_name}:
{latest_output}

Recent conversation (last 5 messages):
{history_text or "None"}

Respond with one of:
{{"action":"inject_prompt","target_session":"<name>","prompt":"..."}}
{{"action":"wait"}}
{{"action":"ask_human","question":"..."}}
"""
```

---

## Testing

### Test Case 1: Multi-Directory Project

**Setup:**
```python
# Create sessions
POST /sessions {"name": "frontend", "workdir": "/app/client"}
POST /sessions {"name": "backend", "workdir": "/app/server"}

# Create orchestrator
POST /orchestrators {
  "name": "App Builder",
  "managed_sessions": ["frontend", "backend"],
  "goal": "Build a web app"
}
```

**Expected:**
Orchestrator prompt should include:
```
Managed sessions and their working directories:
  - frontend: /app/client
  - backend: /app/server
```

### Test Case 2: Session Without Workdir

**Setup:**
```python
POST /sessions {"name": "research"}  # No workdir
POST /orchestrators {
  "managed_sessions": ["research"],
  "goal": "Research AI tools"
}
```

**Expected:**
```
Managed sessions and their working directories:
  - research: (default)
```

### Test Case 3: Mixed Workdirs

**Setup:**
```python
POST /sessions {"name": "frontend", "workdir": "/proj/ui"}
POST /sessions {"name": "backend"}  # No workdir
POST /sessions {"name": "mobile", "workdir": "/proj/app"}
```

**Expected:**
```
Managed sessions and their working directories:
  - frontend: /proj/ui
  - backend: (default)
  - mobile: /proj/app (just became idle)
```

---

## Edge Cases

### 1. **No Managed Sessions**
```
Managed sessions and their working directories:
  (none)
```

### 2. **Session Workdir Changes**
If a session's workdir changes after orchestrator created:
- Orchestrator will see updated workdir on next decision
- ✅ Always shows current state

### 3. **Long Paths**
If workdir paths are very long:
- Consider truncating with `...` in the middle
- Or use relative paths if possible

---

## Alternative: Add to Kickoff Prompt Too

Could also enhance worker kickoff to show ALL sessions:

**Current:**
```
Project goal: Build web app
Session working directory: /app/client

You are the developer...
```

**Enhanced:**
```
Project goal: Build web app

All project sessions:
  - frontend (you): /app/client
  - backend: /app/server
  - docs: /app/documentation

You are the frontend developer...
```

This helps workers know about sibling sessions too!

---

## Recommendation

**Priority: HIGH** ⚠️

This is a critical improvement for orchestrators managing multi-directory projects. Without it, orchestrators can't give context-aware instructions about file paths, imports, or cross-session coordination.

**Effort: LOW** ✅

Simple code change - just collect workdirs for all managed sessions and format them into the prompt.

**Impact: HIGH** 🚀

Dramatically improves orchestrator's ability to:
- Coordinate across microservices
- Reference correct file paths
- Understand project structure
- Give specific, actionable instructions

---

## Files to Modify

1. **app.py** - `_run_orchestrator_decision()` function
2. **core/orchestrator_manager.py** - (optional) `_build_worker_kickoff_prompt()` for worker context

---

## Summary

**Problem**: Orchestrator only knows ONE workdir (current session)
**Impact**: Can't give context-aware instructions to other sessions
**Solution**: Show ALL managed sessions and their workdirs in decision prompt
**Benefit**: Better cross-session coordination and specific file references
**Effort**: ~10 lines of code
