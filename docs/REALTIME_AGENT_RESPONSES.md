# Real-Time Agent Responses Fix

**Date**: 2026-02-08
**Status**: ✅ Fixed

---

## Problem

Orchestrator messages appeared in real-time ✅, but agent responses didn't ❌.

**User Experience:**
1. Orchestrator injects prompt → appears immediately ✅
2. Agent processes and responds → **nothing appears** ❌
3. User refreshes page → agent response finally shows up

---

## Root Cause

When we implemented real-time session messages, we only added broadcasting for **orchestrator injections**, not for **agent responses**.

**Flow:**
1. `_inject_prompt_to_session()` broadcasts orchestrator message ✅
2. Job starts to process the prompt
3. Agent generates response
4. Response saved to history via `_append_history()`
5. **No broadcast sent** ❌ - viewers don't see it

---

## Solution

Added broadcasting for agent responses in all four provider job handlers.

### Changes Made

#### 1. Codex Handler (app.py ~line 2388)
```python
if job.session_id:
    conversation = {"messages": [], "tool_outputs": tool_outputs}
    if job.prompt:
        conversation["messages"].append({"role": "user", "text": job.prompt})
    if assistant_chunks:
        assistant_text = "\n".join(assistant_chunks).strip()
        conversation["messages"].append({"role": "assistant", "text": assistant_text})
    _append_history(job.session_id, job.session_name, conversation)

    # Broadcast agent response to session viewers in real-time
    if assistant_chunks:
        _broadcast_session_message(job.session_name, {
            "type": "message",
            "source": "agent",
            "role": "assistant",
            "text": assistant_text
        })
```

#### 2. Copilot Handler (app.py ~line 2597)
Same pattern - broadcasts agent response after appending to history.

#### 3. Gemini Handler (app.py ~line 2738)
Same pattern - broadcasts agent response after appending to history.

#### 4. Claude Handler (app.py ~line 2912)
Same pattern - broadcasts agent response after appending to history.

---

## How It Works Now

**Complete Real-Time Flow:**

1. **Orchestrator injects prompt**
   - `_inject_prompt_to_session()` called
   - Broadcasts: `{"type": "message", "source": "orchestrator", "role": "system", "text": "[Orchestrator] prompt"}`
   - ✅ Viewers see orchestrator message immediately

2. **Job starts processing**
   - Session status → "running"
   - Agent begins generating response

3. **Agent completes response**
   - Response appended to history
   - **NEW:** Broadcasts: `{"type": "message", "source": "agent", "role": "assistant", "text": "agent response"}`
   - ✅ Viewers see agent response immediately

4. **Session returns to idle**
   - Session status → "idle"
   - Ready for next message

---

## User Experience Now

**Viewing a session when orchestrator injects:**
1. See orchestrator message appear ✅
2. See session status change to "running" ✅
3. See agent response appear when complete ✅
4. See session status return to "idle" ✅
5. **No page refresh needed!** ✅

---

## Message Format

**Orchestrator Message:**
```json
{
  "type": "message",
  "source": "orchestrator",
  "role": "system",
  "text": "[Orchestrator] Please implement the login feature"
}
```

**Agent Response:**
```json
{
  "type": "message",
  "source": "agent",
  "role": "assistant",
  "text": "I'll implement the login feature. Let me start by..."
}
```

Both use the same format, just different `source` values.

---

## Testing

### Manual Test

1. **Setup:**
   - Create a session (e.g., "dev")
   - Create orchestrator managing "dev"
   - Enable orchestrator
   - Open "dev" session in browser

2. **Trigger orchestrator:**
   - Send message to another managed session
   - Orchestrator will inject prompt into "dev"

3. **Verify:**
   - ✅ Orchestrator message appears immediately
   - ✅ Session status → "running"
   - ✅ Agent response appears when complete (without refresh!)
   - ✅ Session status → "idle"

---

## Why This Fix Was Needed

The original implementation only broadcast orchestrator messages because we were focused on solving the specific problem: "orchestrator messages don't appear in real-time."

We didn't realize at the time that agent responses ALSO needed broadcasting, because:
1. Interactive sessions use a different flow (direct SSE connection to job)
2. Orchestrator-initiated jobs run in background (no direct SSE connection)
3. Only history saving happened, no real-time updates

This fix completes the real-time message system by broadcasting BOTH:
- Orchestrator → session messages (already working)
- Agent → viewer messages (now working!)

---

## Impact on All Message Sources

This pattern now works for **all message sources**:

### 1. User → Session (Interactive)
- Uses existing job SSE stream
- Already worked

### 2. Orchestrator → Session (Injection)
- Uses new session message stream
- Now broadcasts both orchestrator message AND agent response ✅

### 3. Task → Session (Scheduled)
- Tasks also use job execution flow
- Now broadcasts agent responses ✅

### 4. Future: User → User (Collaborative)
- Infrastructure ready
- Can broadcast user messages from other viewers

---

## Related Files

- **Backend**: `app.py` (all four provider handlers)
- **Frontend**: `templates/chat.html` (message stream listener)
- **Related Docs**:
  - `docs/REALTIME_SESSION_MESSAGES.md` - Initial implementation
  - `docs/ORCHESTRATOR_WORKDIR_ENHANCEMENT.md` - Orchestrator improvements

---

## Summary

✅ **Fixed**: Agent responses now appear in real-time
✅ **Complete Flow**: Orchestrator message → Agent response (both real-time)
✅ **All Providers**: Codex, Copilot, Gemini, Claude
✅ **No Refresh**: Smooth, interactive experience

Users can now watch the full conversation unfold in real-time without any page refreshes!
