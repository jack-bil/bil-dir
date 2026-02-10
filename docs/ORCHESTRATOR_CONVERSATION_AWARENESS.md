# Orchestrator Conversation Awareness Improvements

**Date**: 2026-02-08
**Status**: ✅ Implemented

---

## Problem

Orchestrators were repeating the same prompts over and over, not realizing they already asked something.

**Example Issue (notes app session):**
```
[Orchestrator] Please create a notes app
[Assistant] I'll create a notes app...
[Orchestrator] Please create a notes app
[Assistant] I'll create a notes app...
[Orchestrator] Please create a notes app
...repeating endlessly...
```

---

## Root Cause

### Issue 1: Unclear Conversation Format

**Old format:**
```
Recent conversation (last 5 messages, if any):
system: [Orchestrator] Please create a notes app
assistant: I'll create a notes app...
user: Can you add authentication?
```

**Problems:**
- "system" role is vague - doesn't clearly indicate it's the orchestrator's own message
- Hard for orchestrator to recognize its own previous prompts
- No clear distinction between actors

### Issue 2: Limited Context

- Only showed last **5 messages**
- Not enough context for complex conversations
- Orchestrator couldn't see full conversation flow

### Issue 3: No Explicit Guidance

- No instruction to avoid repetition
- No guidance to review history before acting
- Orchestrator wasn't prompted to check what it already asked

---

## Solutions Implemented

### Solution 1: Clearer Message Formatting ✅

Modified `_format_recent_history()` (app.py lines 390-428):

```python
def _format_recent_history(session_name, limit=5):
    """Format recent conversation history for orchestrator visibility.

    Returns messages in a clear format that shows who said what,
    so orchestrators can avoid repeating themselves.
    """
    history = _get_history_for_name(session_name)
    messages = history.get("messages") or []
    if not messages:
        return ""
    recent = messages[-limit:]
    lines = []
    for msg in recent:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "assistant"
        text = (msg.get("text") or "").strip()
        if not text:
            continue

        # Format role names clearly so orchestrator knows who said what
        if role == "system":
            # System messages are from orchestrator
            role_label = "Orchestrator"
        elif role == "user":
            role_label = "User"
        elif role == "assistant":
            role_label = "Assistant"
        elif role == "error":
            role_label = "Error"
        else:
            role_label = role.capitalize()

        lines.append(f"[{role_label}] {text}")
    return "\n".join(lines).strip()
```

**New format:**
```
Recent conversation history (last 10 messages):
[Orchestrator] Please create a notes app
[Assistant] I'll create a notes app...
[User] Can you add authentication?
[Orchestrator] Please add authentication to the notes app
[Assistant] I'll add authentication...
```

**Benefits:**
- ✅ Crystal clear who said what
- ✅ Orchestrator can easily see its own previous prompts
- ✅ Clear distinction between all actors (User, Orchestrator, Assistant, Error)

### Solution 2: Increased Context Window ✅

Changed history limit from **5 → 10 messages** (app.py line 440):

```python
history_text = _format_recent_history(session_name, limit=10)
```

**Benefits:**
- ✅ More context for decision-making
- ✅ Can see longer conversation flows
- ✅ Better understanding of what's been accomplished

### Solution 3: Explicit Anti-Repetition Guidance ✅

Added new rules to orchestrator prompt (app.py lines 457-477):

```python
Rules:
- Output exactly ONE JSON object.
- Do not include any other keys, commentary, or metadata.
- Review the conversation history above to avoid repeating yourself.
- If you already asked something and got a response, move forward with the next step.
- If unsure, return {"action":"wait"}.
- Prefer inject_prompt over ask_human.
- Do NOT ask the human to choose an orchestrator.
- If you inject, target_session MUST be the managed session name shown above.
```

**Key additions:**
- "Review the conversation history above to avoid repeating yourself"
- "If you already asked something and got a response, move forward with the next step"

---

## How It Works Now

### Orchestrator Decision Prompt (Enhanced)

```
TASK: Decide the next action for orchestrator "App Builder". Respond with ONLY valid JSON.

Manager instructions:
Act as the manager across any task type...

Goal:
Build a notes application

Managed sessions and their working directories:
  - notes app: C:/Users/jack/Projects/notes (just became idle)

This orchestrator ONLY manages these sessions: notes app.
Managed session just became idle: notes app
Latest output:
I'll create a basic notes app with create, read, update, and delete functionality...

Recent conversation history (last 10 messages):
[Orchestrator] Please create a notes app with basic CRUD operations
[Assistant] I'll create a basic notes app with create, read, update, and delete functionality...

Respond with one of:
{"action":"inject_prompt","target_session":"<name>","prompt":"..."}
{"action":"wait"}
{"action":"ask_human","question":"..."}

Rules:
- Output exactly ONE JSON object.
- Do not include any other keys, commentary, or metadata.
- Review the conversation history above to avoid repeating yourself.
- If you already asked something and got a response, move forward with the next step.
- If unsure, return {"action":"wait"}.
- Prefer inject_prompt over ask_human.
- Do NOT ask the human to choose an orchestrator.
- If you inject, target_session MUST be the managed session name shown above.
```

**Orchestrator can now see:**
- ✅ Its own previous prompt: "[Orchestrator] Please create a notes app..."
- ✅ The assistant's response to that prompt
- ✅ Clear instruction to avoid repetition
- ✅ Guidance to move forward with next step

---

## Expected Behavior After Fix

### Before (Repetitive):
```
Decision 1:
{"action":"inject_prompt","target_session":"notes app","prompt":"Create a notes app"}

Decision 2:
{"action":"inject_prompt","target_session":"notes app","prompt":"Create a notes app"}

Decision 3:
{"action":"inject_prompt","target_session":"notes app","prompt":"Create a notes app"}
```

### After (Progressive):
```
Decision 1:
{"action":"inject_prompt","target_session":"notes app","prompt":"Create a basic notes app with CRUD operations"}

Decision 2:
{"action":"inject_prompt","target_session":"notes app","prompt":"Add user authentication to the notes app"}

Decision 3:
{"action":"inject_prompt","target_session":"notes app","prompt":"Add styling with CSS to make it look professional"}
```

---

## Message Format Examples

### System/Orchestrator Messages
```
[Orchestrator] [Orchestrator] Please implement feature X
```
Note: The `[Orchestrator]` prefix appears twice because:
1. First one is added by the role label formatting
2. Second one is part of the message text (saved with the prefix)

We could clean this up, but it's clear enough.

### User Messages
```
[User] Can you add authentication?
```

### Assistant Messages
```
[Assistant] I'll implement the authentication feature...
```

### Error Messages
```
[Error] Error: claude exec timed out
```

---

## Testing

### Manual Test

1. **Setup:**
   - Create orchestrator managing a session
   - Enable orchestrator

2. **Let it run for multiple decisions:**
   - Orchestrator injects prompt
   - Session responds
   - Orchestrator triggered again

3. **Verify no repetition:**
   - ✅ Each prompt should move forward
   - ✅ No asking the same thing twice
   - ✅ Progressive task completion

4. **Check conversation history in prompt:**
   - Look at orchestrator decision logs
   - Verify history shows `[Orchestrator]`, `[Assistant]`, etc.
   - Verify 10 messages shown (when available)

---

## Additional Benefits

### Better Debugging
- Clear conversation format makes debugging easier
- Can see exactly what orchestrator was told
- Can identify where conversations go wrong

### More Context
- 10 messages instead of 5
- Better understanding of conversation flow
- Can handle longer, more complex interactions

### Explicit Guidance
- Orchestrator knows to check history
- Reduces wasted API calls
- More efficient task completion

---

## Future Enhancements

### Could Add:
1. **Timestamps** - Show when each message was sent
2. **Message IDs** - Track specific exchanges
3. **Conversation summary** - AI-generated summary of progress
4. **State tracking** - Explicit state of what's been done
5. **Deduplication** - Automatic detection of repeated prompts

---

## Related Documentation

- `docs/ORCHESTRATOR_WORKDIR_ENHANCEMENT.md` - Session workdir visibility
- `docs/ERROR_PERSISTENCE_AND_ORCHESTRATOR_NOTIFICATIONS.md` - Error handling
- `docs/REALTIME_SESSION_MESSAGES.md` - Real-time messaging

---

## Summary

✅ **Clearer formatting** - `[Orchestrator]`, `[User]`, `[Assistant]` labels
✅ **More context** - 10 messages instead of 5
✅ **Anti-repetition guidance** - Explicit rules to avoid repeating
✅ **Better decision-making** - Orchestrators can see full conversation flow
✅ **Progressive execution** - Move forward instead of repeating

Orchestrators now understand conversation context and avoid repeating themselves! 🎉
