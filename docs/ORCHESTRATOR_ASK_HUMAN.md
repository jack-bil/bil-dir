# Orchestrator ask_human Implementation

**Date**: 2026-02-08
**Status**: ✅ Implemented

---

## Overview

Orchestrators can now ask humans for approval or clarification using the `ask_human` action. Questions appear in the master console as if the **session** is asking (not the orchestrator), making it clear where responses should go.

---

## User Experience

### Scenario: Session Wants to Take Significant Action

**1. Session asks for approval:**
```
[Assistant] I can't fix the problem. I'm going to git pull and overwrite the code, ok?
```
Session goes **idle**

**2. Orchestrator sees this and escalates:**
Orchestrator receives decision prompt with latest output: "I'm going to git pull and overwrite the code, ok?"

Orchestrator decides:
```json
{
  "action": "ask_human",
  "question": "Session wants to git pull and overwrite local code. Approve?"
}
```

**3. Question appears in master console:**
```
┌─────────────────────────────────────────────────────┐
│ @@backend asks:                                     │
│                                                     │
│ Session wants to git pull and overwrite local      │
│ code. Approve?                                      │
│                                                     │
│ [Your response: ___________________] [Send]         │
└─────────────────────────────────────────────────────┘
```

**Note:** Displayed as `@@backend` (session), NOT `@@orchestrator`

**4. User responds:**
You type: **"No, try resetting just the config file first"**
Press Enter or click Send

**5. Response injected to session:**
```
[User] No, try resetting just the config file first
```

**6. Session continues working:**
```
[Assistant] Good idea. I'll reset just the config file instead...
[Assistant] Config file reset. The issue is fixed now.
```

Session goes **idle**

**7. Orchestrator continues coordination:**
Orchestrator sees full conversation history and makes next decision.

---

## Implementation Details

### Backend (app.py)

#### 1. Modified ask_human Handler (lines ~3300-3326)
```python
if action_type == "ask_human":
    question = action.get("question") or ""
    if question:
        # Save pending question to orchestrator state
        with _ORCH_LOCK:
            data = _load_orchestrators()
            current = data.get(orch_id) or orch
            current["pending_question"] = {
                "question": question,
                "target_session": name,
                "asked_at": now_iso
            }
            data[orch_id] = current
            _save_orchestrators(data)

        # Broadcast to master console
        _broadcast_master_message(name, {
            "type": "orchestrator_question",
            "session_name": name,
            "orchestrator_id": orch_id,
            "orchestrator_name": orch.get("name") or "",
            "question": question
        })
```

**Key changes:**
- No longer converts `ask_human` to `inject_prompt`
- Saves question to orchestrator state
- Broadcasts to master console with special message type

#### 2. New Endpoint: POST /orchestrators/<id>/respond (lines ~3832-3870)
```python
@APP.post("/orchestrators/<orch_id>/respond")
def respond_to_orchestrator(orch_id):
    """User responds to an orchestrator's ask_human question."""
    payload = request.get_json() or {}
    response = (payload.get("response") or "").strip()

    if not response:
        return jsonify({"error": "response required"}), 400

    with _ORCH_LOCK:
        data = _load_orchestrators()
        orch = data.get(orch_id)
        if not orch:
            return jsonify({"error": "orchestrator not found"}), 404

        pending = orch.get("pending_question")
        if not pending:
            return jsonify({"error": "no pending question"}), 400

        target_session = pending.get("target_session")

        # Inject user response to the target session
        _inject_prompt_to_session(target_session, response)

        # Clear pending question
        orch.pop("pending_question", None)
        data[orch_id] = orch
        _save_orchestrators(data)

    return jsonify({"ok": True})
```

#### 3. Updated _broadcast_master_message (lines ~1100-1125)
Now accepts both string (simple message) and dict (structured payload) for flexibility.

### Frontend (templates/chat.html)

#### 1. Master Stream Handler (lines ~2720-2724)
```javascript
} else if (data.type === "orchestrator_question") {
  addOrchestratorQuestion(data);
}
```

#### 2. New Function: addOrchestratorQuestion (lines ~2583-2638)
Creates the UI for displaying questions and capturing responses:
- Shows session name with color coding (`@@session`)
- Displays question text
- Input field for response
- Send button and Enter key support
- Replaces UI with confirmation after sending

#### 3. New Function: sendOrchestratorResponse (lines ~2640-2662)
Sends user's response to backend:
```javascript
async function sendOrchestratorResponse(orchestratorId, response, container) {
  const res = await fetch(`/orchestrators/${orchestratorId}/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response: trimmed })
  });

  if (res.ok) {
    // Show confirmation
    container.innerHTML = `
      <div class="orch-question-answered">
        <strong>You responded:</strong> ${escapeHtml(trimmed)}
      </div>
    `;
  }
}
```

#### 4. CSS Styling (lines ~911-973)
- Blue-tinted question card
- Session-colored left border
- Input field and send button styling
- Green confirmation message when answered

---

## Design Principles

### 1. Session-Centric Display
Questions appear as `@@session` asking, not `@@orchestrator`, because:
- User's response goes to the **session**, not orchestrator
- Creates clear mental model
- Orchestrator is transparent security layer
- No confusion about response routing

### 2. Orchestrator as Gatekeeper
- Orchestrator intercepts session requests
- Recognizes significant/destructive actions
- Escalates to human
- Routes human's answer back to session
- Continues coordination after resolution

### 3. Non-Blocking Flow
- Orchestrator makes decision (ask_human)
- Question saved and displayed
- Orchestrator doesn't make new decisions until answered
- User responds when ready
- Session receives response and continues
- Orchestrator sees resolution in conversation history

---

## When to Use ask_human

### Recommended in Orchestrator Base Prompt
```
Use ask_human when:
- You need clarification on requirements or goals
- The session encountered an error you cannot resolve by retrying
- A decision requires user input or approval
- The scope is unclear or ambiguous
```

### Examples of Good Use Cases

**1. Destructive Actions:**
- Deleting files
- Force pushing code
- Dropping database tables
- Overwriting existing work

**2. Ambiguous Decisions:**
- Choice between multiple valid approaches
- Technology selection (MongoDB vs PostgreSQL)
- Architecture decisions

**3. Error Recovery:**
- Repeated failures of same approach
- Timeout issues requiring different strategy
- Unclear error messages

**4. Scope Clarification:**
- User's goal is vague
- Multiple interpretations possible
- Need priority guidance

---

## Orchestrator State

### Pending Question Format
```json
{
  "pending_question": {
    "question": "Session wants to git pull. Approve?",
    "target_session": "backend",
    "asked_at": "2026-02-08T10:30:00"
  }
}
```

### Cleared After Response
When user responds, `pending_question` is removed from orchestrator state.

---

## Message Flow Diagram

```
┌─────────────────────────────────────────────────────┐
│ Session: "I'm going to git pull, ok?"              │
└─────────────────────────────────────────────────────┘
                    ↓ (idle)
┌─────────────────────────────────────────────────────┐
│ Orchestrator: Sees message, recognizes risk        │
│ Decision: {"action":"ask_human","question":"..."}   │
└─────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────┐
│ Master Console: @@backend asks: [question]         │
│                 [Your response: ___] [Send]         │
└─────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────┐
│ User: Types response, clicks Send                  │
└─────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────┐
│ Backend: POST /orchestrators/<id>/respond          │
│          Injects response to session                │
└─────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────┐
│ Session: [User] No, try resetting config first     │
│          Processes response, continues work         │
└─────────────────────────────────────────────────────┘
                    ↓ (idle after processing)
┌─────────────────────────────────────────────────────┐
│ Orchestrator: Sees conversation with user response │
│               Makes next decision                   │
└─────────────────────────────────────────────────────┘
```

---

## Testing

### Manual Test

**1. Setup:**
- Create a session (e.g., "test-session")
- Create orchestrator managing that session
- Enable orchestrator

**2. Trigger ask_human:**
Option A: Have session output something that needs approval
Option B: Manually test with simple scenario:
```
Session outputs: "I need to delete old files. Should I proceed?"
```

**3. Verify orchestrator asks:**
- Check master console
- Should see: `@@test-session asks: [question]`
- Input field should be present

**4. Respond:**
- Type response: "No, archive them instead"
- Click Send or press Enter

**5. Verify response injected:**
- Open test-session
- Should see: `[User] No, archive them instead`
- Session should respond to the user message

**6. Verify orchestrator continues:**
- After session responds and goes idle
- Orchestrator should make next decision
- Should see full conversation history in orchestrator context

---

## Files Modified

### Backend
- `app.py`:
  - Modified `ask_human` handler (~3300-3326)
  - Updated `_broadcast_master_message` (~1100-1125)
  - Added `POST /orchestrators/<id>/respond` (~3832-3870)

### Frontend
- `templates/chat.html`:
  - Updated master stream handler (~2720-2724)
  - Added `addOrchestratorQuestion` function (~2583-2638)
  - Added `sendOrchestratorResponse` function (~2640-2662)
  - Added CSS styling (~911-973)

---

## Related Documentation

- `docs/ORCHESTRATOR_CONVERSATION_AWARENESS.md` - Orchestrator history/context
- `docs/ERROR_PERSISTENCE_AND_ORCHESTRATOR_NOTIFICATIONS.md` - Error handling
- `docs/REALTIME_SESSION_MESSAGES.md` - Real-time messaging infrastructure

---

## Summary

✅ **ask_human now works** - Questions displayed in master console
✅ **Session-centric display** - Shows as `@@session` asking
✅ **Human responses routed** - Injected to session, not orchestrator
✅ **Non-blocking flow** - Orchestrator waits for response, then continues
✅ **Clean UX** - Input field, send button, confirmation message
✅ **Full context preserved** - Orchestrator sees complete conversation

Orchestrators can now safely escalate decisions to humans while maintaining smooth coordination! 🎉
