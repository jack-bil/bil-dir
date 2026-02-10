# Context Briefing System Analysis - 2026-02-07

## Summary

The context briefing system IS implemented in the code but **may not be working correctly** due to a timing/session ID issue.

---

## How It's SUPPOSED to Work

### Flow:
1. User sends message to **Provider A** (e.g., Codex) in session "test"
2. Provider A responds
3. Session ID for Provider A is saved
4. User switches to **Provider B** (e.g., Claude)
5. System detects provider change
6. **IF** Provider B doesn't have a session ID yet **AND** Provider A has a session ID:
   - Generate summary of Provider A's conversation
   - Save summary to `context/{session_name}_context.md`
7. When starting Provider B:
   - Load context from `context/{session_name}_context.md`
   - Inject into prompt as "Previous conversation history"
8. Provider B sees the context and can continue the conversation

---

## What I Found

### ✅ Code IS in Place

**Context Generation** (app.py lines 3259-3287):
```python
if session_name and current_provider_before and provider != current_provider_before:
    if not new_provider_session_id and current_session_id_before:
        # Generate summary using old provider
        summary = _generate_session_summary(...)
        _append_context_briefing(session_name, summary, ...)
```

**Context Loading** (app.py lines 3290-3301):
```python
if session_name:
    if not provider_has_session:
        context_briefing = _load_session_context(session_name)
```

**Context Injection** (app.py - all providers):
- Codex: lines 161-171, 3520-3530
- Copilot: lines 1922-1934, 3790-3800
- Gemini: lines 2007-2017, 3984-3994
- Claude: lines 2381-2391, 4109-4119

### ❌ **PROBLEM DETECTED**

From `context_debug.log`:
```
[Context] Provider changed: codex -> claude
[Context] New provider session_id: None  ← Correct, Claude has no session yet
[Context] Not generating: new_session_id=None, old_session_id=None  ← BUG!
```

**The Issue**: `old_session_id=None`

This means when switching providers, the system can't find the session ID for the **previous** provider (Codex in this example).

---

## Root Cause Analysis

### Why `current_session_id_before` is None

**Code that gets it** (app.py lines 3232-3242):
```python
current_provider_before = _get_session_provider_for_name(session_name)
session_ids = record.get("session_ids") or {}
current_session_id_before = session_ids.get(current_provider_before)
```

### Possible Reasons:

#### 1. **Session ID Not Being Saved**
After a message completes, the session ID should be saved:
```python
if session_name and result.get("session_id"):
    _set_session_name(session_name, result["session_id"], provider)
```

**Check**: Is `_set_session_name()` actually saving to `session_ids`?

#### 2. **Provider Switching Before First Response**
If user switches providers BEFORE getting the first response:
- No session ID has been created yet
- `current_session_id_before` would be None
- Summary wouldn't be generated

**This is valid behavior** but means no context on first switch.

#### 3. **Session Structure Issue**
The session record should look like:
```json
{
  "test_session": {
    "provider": "codex",
    "session_ids": {
      "codex": "019c2b8d-...",
      "claude": "088abaca-..."
    }
  }
}
```

**Check**: Are `session_ids` being stored correctly?

---

## Evidence from Logs

### What's Working:
✅ Provider change detection: `[Context] Provider changed: codex -> claude`
✅ Context file attempts: `[Context] No context file found for session X`
✅ Injection code exists: Checked all providers

### What's NOT Working:
❌ Summary generation never happens
❌ Context files never created (directory doesn't exist)
❌ `old_session_id` always None when switching

---

## Directory Status

```bash
$ ls context/
ls: cannot access 'context/': No such file or directory
```

**Expected**: Directory should exist with `.md` files like:
- `test_session_context.md`
- `my_project_context.md`

**Reality**: Directory doesn't exist because summaries never generated.

---

## Investigation Needed

### 1. Check `_set_session_name()` Function
**Question**: Does it properly save to `session_ids` dict?

**Expected behavior**:
```python
def _set_session_name(name, session_id, provider):
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        session_ids = record.get("session_ids") or {}
        session_ids[provider] = session_id  # ← Does this happen?
        record["session_ids"] = session_ids
        data[name] = record
        _save_sessions(data)
```

### 2. Check Session JSON Structure
**File**: `sessions.json`

**Command**: `cat sessions.json | jq`

**Look for**: Do sessions have `session_ids` dict with provider keys?

### 3. Test Flow Manually

**Steps**:
1. Create new session "context_test"
2. Send message to Codex
3. Wait for response
4. Check `sessions.json` - should have codex session ID
5. Switch to Claude
6. Check logs for "[Context] Provider changed"
7. Check if `old_session_id` is found
8. Check if summary generated
9. Check if `context/context_test_context.md` created

---

## Hypothesis

**Most Likely Issue**: The `_set_session_name()` function might not be saving session IDs correctly into the `session_ids` dictionary, OR it's saving them but using a different key structure.

**Why this matters**:
- Without session IDs in `session_ids[provider]`, the system can't detect previous provider sessions
- Without detection, no summaries are generated
- Without summaries, no context files
- Without context files, no context injection

---

## Quick Fixes to Try (DO NOT APPLY YET)

### Option 1: Add Debug Logging
Add after line 3242:
```python
logger.info(f"[Context DEBUG] session_ids: {session_ids}")
logger.info(f"[Context DEBUG] current_provider_before: {current_provider_before}")
logger.info(f"[Context DEBUG] current_session_id_before: {current_session_id_before}")
```

### Option 2: Check _set_session_name Implementation
Look for the function and verify it's saving correctly.

### Option 3: Manual Test
Create a context file manually and see if injection works:
```bash
mkdir context
echo "## Previous conversation\n\nUser asked about cats." > context/test_session_context.md
```

Then switch providers and see if it loads.

---

## Next Steps

1. **Examine `_set_session_name()` implementation**
2. **Check `sessions.json` structure**
3. **Add debug logging to trace session ID flow**
4. **Test with manual context file**
5. **Fix session ID storage if broken**

---

## Files to Review

- `app.py` lines 1500-1550: Session ID management functions
- `sessions.json`: Current session structure
- `context_debug.log`: Full context flow logs
- `_set_session_name()` implementation

---

## Conclusion

**System Status**: 🟡 **Partially Implemented**

- ✅ Code structure is correct
- ✅ Logic flow is sound
- ❌ Session ID tracking appears broken
- ❌ No summaries are being generated
- ❌ No context files exist

**Likely Fix**: Repair `_set_session_name()` or session ID storage mechanism.
