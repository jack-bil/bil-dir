# Error Persistence and Orchestrator Notifications

**Date**: 2026-02-08
**Status**: ✅ Implemented

---

## Problems Solved

### Problem 1: Errors Disappear on Page Refresh ❌

**User Experience Before:**
1. Error occurs (e.g., "claude exec timed out")
2. Error shows in chat via SSE
3. Refresh page → **error is gone**
4. No context about what went wrong

**Why:**
- `_broadcast_error()` only sent errors via SSE
- Errors were NOT saved to history
- No persistence across page reloads

### Problem 2: Orchestrators Unaware of Errors ❌

**Behavior Before:**
1. Managed session encounters error
2. Session goes to "idle"
3. Orchestrator triggered
4. Orchestrator sees session is idle but **doesn't know there was an error**
5. Might inject the same problematic prompt again

**Why:**
- `_get_latest_assistant_message_with_index()` only looked for "assistant" role
- Errors weren't included in "latest output"
- Orchestrator couldn't react to failures

---

## Solutions Implemented

### Solution 1: Save Errors to History ✅

Modified `_broadcast_error()` to persist errors (app.py lines 2228-2253):

```python
def _broadcast_error(job, text):
    _log_event(
        {
            "type": "job.error",
            "provider": job.provider,
            "session_name": job.session_name,
            "session_id": job.session_id,
            "prompt": job.prompt,
            "message": text,
        }
    )

    # Save error to history so it persists across page refreshes
    if job.session_id and text:
        _append_history(
            job.session_id,
            job.session_name,
            {"messages": [{"role": "error", "text": f"Error: {text}"}], "tool_outputs": []},
        )

    # Broadcast to session viewers in real-time
    if job.session_name and text:
        _broadcast_session_message(job.session_name, {
            "type": "message",
            "source": "system",
            "role": "error",
            "text": f"Error: {text}"
        })

    # Also send via job SSE for direct connections
    job.broadcast(f"event: error\ndata: {text}\n\n")
```

**What This Does:**
1. **Saves to history** → Errors persist across page refreshes ✅
2. **Broadcasts to session viewers** → Real-time error display ✅
3. **Maintains job SSE** → Direct connections still work ✅

### Solution 2: Style Error Messages ✅

Added CSS styling for error messages (templates/chat.html):

```css
.msg.error {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  line-height: 1.6;
}
```

**Visual Design:**
- Red-tinted background
- Red border
- Light red text
- Clearly distinguishable from normal messages

### Solution 3: Include Errors in Orchestrator Latest Output ✅

Modified `_get_latest_assistant_message_with_index()` to include errors (app.py lines 370-387):

```python
def _get_latest_assistant_message_with_index(session_name):
    """Get the latest assistant message or error from session history.

    This is used by orchestrators to see the latest output from a session,
    including error messages so they can react appropriately.
    """
    if not session_name:
        return -1, ""
    history = _get_history_for_name(session_name)
    messages = history.get("messages") or []
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict):
            role = msg.get("role")
            # Include both assistant messages and error messages
            if role in ("assistant", "error"):
                return idx, msg.get("text") or ""
    return -1, ""
```

**What This Does:**
- Orchestrator sees errors in "Latest output" section
- Can make informed decisions based on failures
- Enables error recovery strategies

---

## How It Works Now

### Scenario 1: Session Error Without Orchestrator

**User viewing session when error occurs:**
1. Error happens (e.g., timeout)
2. ✅ Error message appears in real-time
3. ✅ Error styled in red with clear formatting
4. ✅ Refresh page → error still visible
5. ✅ Full context preserved

### Scenario 2: Managed Session Error With Orchestrator

**Orchestrator managing a session that errors:**
1. Orchestrator injects prompt
2. Session processes → encounters error
3. Error saved to history
4. Session goes to "idle"
5. Orchestrator triggered for decision
6. **Orchestrator sees error in prompt:**
   ```
   Latest output:
   Error: claude exec timed out
   ```
7. Orchestrator can decide how to handle:
   - Retry with simpler prompt
   - Ask human for help
   - Switch to different provider
   - Try alternative approach

---

## Orchestrator Decision Example

**With Error Context:**
```
TASK: Decide the next action for orchestrator "App Builder". Respond with ONLY valid JSON.

Manager instructions:
Act as the manager...

Goal:
Build a full-stack application

Managed sessions and their working directories:
  - frontend: C:/app/client
  - backend: C:/app/server (just became idle)
  - docs: C:/app/documentation

Session that just became idle: backend
Latest output:
Error: claude exec timed out

Recent conversation (last 5 messages, if any):
[User] Implement the database schema
[Orchestrator] Please implement the PostgreSQL schema
Error: claude exec timed out

Respond with one of:
{"action":"inject_prompt","target_session":"<name>","prompt":"..."}
{"action":"wait"}
{"action":"ask_human","question":"..."}
```

**Orchestrator can now respond intelligently:**
```json
{
  "action": "ask_human",
  "question": "The backend session timed out while trying to implement the database schema. Should I retry with a simpler prompt, or would you like to handle this manually?"
}
```

Or:
```json
{
  "action": "inject_prompt",
  "target_session": "backend",
  "prompt": "The previous attempt timed out. Let's start with just the basic user table schema. Keep it simple."
}
```

---

## Error Types That Persist

**All error types are now saved to history:**
- `claude exec timed out`
- `codex exec timed out`
- `copilot exec timed out`
- `gemini exec timed out`
- `<provider> CLI not found in PATH`
- `<provider> CLI failed`
- Any custom error messages

---

## Benefits

### For Users:
1. ✅ **Context Preservation** - Errors don't disappear
2. ✅ **Debugging** - Can review error history
3. ✅ **Awareness** - Always know what went wrong
4. ✅ **Real-Time + Persistent** - See errors immediately AND after refresh

### For Orchestrators:
1. ✅ **Error Awareness** - Know when managed sessions fail
2. ✅ **Intelligent Recovery** - Can retry or adjust approach
3. ✅ **Human Escalation** - Can ask for help when stuck
4. ✅ **Avoid Loops** - Won't repeat same failing prompt endlessly

---

## About Timeouts

### Current Timeout Settings

**Orchestrator Injections:**
- Hardcoded: **300 seconds (5 minutes)**
- Location: `_inject_prompt_to_session()` line 564

**Interactive Sessions:**
- Default: **300 seconds (5 minutes)**
- Can be configured per provider

**Tasks:**
- Default: **900 seconds (15 minutes)**
- Configurable per task via `timeout_sec` field

### Why Timeouts Happen

**Common Causes:**
1. **Complex tasks** - Agent needs more time
2. **Network issues** - API calls taking too long
3. **CLI hanging** - Provider process stuck
4. **Resource limits** - System constraints

### Potential Improvements

**Future enhancements could include:**
1. **Configurable timeouts per session**
2. **Dynamic timeouts based on prompt complexity**
3. **Retry logic with exponential backoff**
4. **Provider fallback on timeout**

---

## Testing

### Manual Test: Error Persistence

1. **Trigger an error:**
   - Send a very complex prompt that might timeout
   - Or intentionally break CLI access

2. **Verify real-time display:**
   - ✅ Error appears immediately
   - ✅ Styled in red

3. **Verify persistence:**
   - Refresh page
   - ✅ Error still visible in history
   - ✅ Context preserved

### Manual Test: Orchestrator Error Handling

1. **Setup:**
   - Create orchestrator managing a session
   - Enable orchestrator

2. **Trigger error:**
   - Have orchestrator inject a prompt that will timeout
   - Or inject a prompt that causes an error

3. **Verify orchestrator response:**
   - ✅ Orchestrator receives error in "latest output"
   - ✅ Can see error in orchestrator decision prompt
   - ✅ Orchestrator can respond appropriately

---

## Message Format

**Error Message in History:**
```json
{
  "role": "error",
  "text": "Error: claude exec timed out"
}
```

**Error Broadcast to Viewers:**
```json
{
  "type": "message",
  "source": "system",
  "role": "error",
  "text": "Error: claude exec timed out"
}
```

---

## Files Modified

### Backend (app.py)
- `_broadcast_error()` - Save errors to history + broadcast
- `_get_latest_assistant_message_with_index()` - Include errors in latest output

### Frontend (templates/chat.html)
- CSS styling for `.msg.error`
- Already handles "error" role in `addMessage()`

---

## Related Documentation

- `docs/REALTIME_SESSION_MESSAGES.md` - Session message streaming
- `docs/REALTIME_AGENT_RESPONSES.md` - Agent response broadcasting
- `docs/ORCHESTRATOR_WORKDIR_ENHANCEMENT.md` - Orchestrator improvements

---

## Summary

✅ **Errors now persist** - Saved to history, visible on refresh
✅ **Errors styled clearly** - Red background/border for visibility
✅ **Orchestrators aware** - Can see and react to session errors
✅ **Real-time + persistent** - Best of both worlds
✅ **Better debugging** - Full error context preserved
✅ **Intelligent recovery** - Orchestrators can handle failures gracefully

Users and orchestrators now have complete visibility into errors, enabling better debugging and intelligent error recovery! 🎉
