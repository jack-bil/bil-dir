# Orchestrator Feature Requirements

## Overview

Add orchestration capabilities to Bil-dir that enable AI-coordinated multi-agent workflows. An orchestrator is a special entity that monitors and coordinates multiple sessions, enabling autonomous collaboration between specialized AI agents (e.g., developer + tester sessions working together).

### Goals

- **Multi-agent coordination**: One orchestrator manages 1+ sessions as a team
- **Autonomous handoffs**: Orchestrator detects when work is complete and assigns next steps
- **Human-in-loop**: Orchestrator escalates questions it cannot answer to the user
- **Context-aware**: Orchestrator reads project documentation to understand goals
- **Session-as-tool**: Sessions are treated as specialized tools with defined roles
- **Event-driven**: React to session state changes via SSE (no polling)
- **Non-invasive**: Fully backwards compatible with existing platform

### Use Case Example

**Setup:**
- Session "dev" (Claude): Python backend development
- Session "test" (Codex): Playwright testing
- Orchestrator "backend-team": Coordinates dev + test

**Flow:**
1. User tells orchestrator: "Implement login feature"
2. Orchestrator reads README.md and requirements.md
3. Orchestrator injects to dev: "Implement login per requirements.md"
4. Dev works, goes idle when done
5. Orchestrator reads dev's work, injects to test: "Test login in src/auth.py"
6. Test finds bug, goes idle
7. Orchestrator injects to dev: "Fix auth bug (test failed with error X)"
8. Loop continues until tests pass
9. Orchestrator reports to user: "Login feature complete and tested"

---

## Core Concepts

### Orchestrator vs Session

**Sessions:**
- Isolated conversation with a provider
- Can only see their own history
- Tied to a working directory
- User interacts directly

**Orchestrators:**
- Coordinate multiple sessions
- Can read all managed session histories
- Has own provider and conversation history
- Can inject prompts into managed sessions
- Event-driven (monitors session status changes)
- Separate UI section (not listed as sessions)

### Session as Tool

Orchestrators view sessions as specialized tools:
```
Available tools:
- dev_session (Python developer): "Implements backend features in Python"
- test_session (QA tester): "Writes and runs Playwright tests"
```

When orchestrator decides to act, it "invokes" a session by injecting a prompt.

---

## Implementation Phases

### Phase 1: Data Model Foundation (1-2 days)

#### 1.1 Session Model Extensions

**File:** `.codex_sessions.json`

**Add new optional fields:**
```json
{
  "session_name": {
    "session_id": "...",
    "provider": "claude",
    "workdir": "/path/to/project",
    "orchestrator_id": null,        // NEW: ID of managing orchestrator (null if none)
    "tags": [],                     // NEW: Tags for categorization ["python", "backend"]
    "description": ""               // NEW: Role description for orchestrator
  }
}
```

**Implementation notes:**
- All new fields are optional (backwards compatible)
- Use `.get("orchestrator_id")` with None default everywhere
- Old sessions without these fields continue working
- Validation: Only one orchestrator per session (enforce on assignment)

**Functions to modify:**
```python
def _load_sessions():
    # Already exists - no changes needed (handles missing fields)

def _save_sessions(data):
    # Already exists - no changes needed
```

#### 1.2 Orchestrator Storage

**File:** `.codex_orchestrators.json` (NEW)

**Structure:**
```json
{
  "orch_abc123": {
    "id": "orch_abc123",
    "name": "backend-team",
    "provider": "claude",
    "provider_session_id": "session_xyz",  // For --resume
    "managed_sessions": ["dev", "test"],
    "working_directory": "/path/to/project",
    "status": "idle",                       // idle, active, paused
    "created_at": "2026-02-01T14:00:00Z",
    "context_files_scanned": [
      "README.md",
      "requirements.md",
      "docs/architecture.md"
    ],
    "system_prompt": "You are an orchestrator..."  // Auto-generated
  }
}
```

**Storage location:** Same directory as `.codex_sessions.json` (working directory)

**New functions:**
```python
def _load_orchestrators(workdir=None):
    """Load orchestrators from .codex_orchestrators.json

    Args:
        workdir: Working directory (default: current working directory)

    Returns:
        dict: Orchestrator ID -> orchestrator object
    """
    path = os.path.join(workdir or os.getcwd(), ".codex_orchestrators.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def _save_orchestrators(data, workdir=None):
    """Save orchestrators to .codex_orchestrators.json

    Args:
        data: Orchestrator data dict
        workdir: Working directory (default: current working directory)
    """
    path = os.path.join(workdir or os.getcwd(), ".codex_orchestrators.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
```

#### 1.3 Message Format Extensions

**Current format:**
```json
{
  "role": "user" | "assistant",
  "text": "message content"
}
```

**New format (backwards compatible):**
```json
{
  "role": "user" | "assistant" | "orchestrator",
  "text": "message content",
  "timestamp": "2026-02-01T14:30:00Z",         // NEW: ISO format UTC
  "source": "human" | "orchestrator:orch_id"  // NEW: Message origin
}
```

**Implementation:**
- Modify all message creation to include `timestamp` and `source`
- Old messages without these fields still render correctly
- Display logic uses `.get("timestamp")` and `.get("source")` with defaults

**Functions to modify:**
```python
def _create_message(role, text, source="human"):
    """Create message with timestamp and source

    Args:
        role: "user", "assistant", or "orchestrator"
        text: Message content
        source: "human" or "orchestrator:orch_id"

    Returns:
        dict: Message object
    """
    return {
        "role": role,
        "text": text,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": source
    }
```

Update these locations:
- `app.py:984` - Gemini message creation
- `app.py:1002` - Assistant response
- `app.py:1904` - Task message creation
- All other locations where messages are appended

#### 1.4 History Storage Consistency

**Orchestrator history uses SAME format as sessions:**

Stored in `.codex_history.json` (per working directory):
```json
{
  "session_id_123": {
    "session_id": "session_id_123",
    "session_name": "dev_session",
    "messages": [...],
    "tool_outputs": [...]
  },
  "orch_id_456": {
    "session_id": "orch_provider_session_id",
    "orchestrator_id": "orch_id_456",        // NEW: Distinguishes orchestrator history
    "orchestrator_name": "backend-team",
    "messages": [...],                       // Same format as session messages
    "tool_outputs": [...]
  }
}
```

**Benefits:**
- Single parser for all conversation history
- Easy to query across sessions and orchestrators
- Minimal code changes

---

### Phase 2: Backend Orchestrator Functions (2-3 days)

#### 2.1 Orchestrator CRUD Operations

```python
def _generate_orchestrator_id():
    """Generate unique orchestrator ID

    Returns:
        str: Unique ID like "orch_abc123def"
    """
    import secrets
    return f"orch_{secrets.token_hex(6)}"

def _create_orchestrator(name, provider, workdir, managed_sessions_config, goal=None):
    """Create new orchestrator

    Args:
        name: Orchestrator name
        provider: AI provider (claude, codex, copilot, gemini)
        workdir: Working directory
        managed_sessions_config: List of dicts with session_name, description, tags
            Example: [
                {"session_name": "dev", "description": "Python developer", "tags": ["python"]},
                {"session_name": "test", "description": "Playwright tester", "tags": ["testing"]}
            ]
        goal: Optional initial goal/objective

    Returns:
        dict: Created orchestrator object

    Raises:
        ValueError: If session already assigned, session doesn't exist, etc.
    """
    # 1. Validate inputs
    orch_id = _generate_orchestrator_id()
    sessions = _load_sessions(workdir)

    # Check all sessions exist and aren't already assigned
    for config in managed_sessions_config:
        session_name = config["session_name"]
        if session_name not in sessions:
            raise ValueError(f"Session '{session_name}' does not exist")
        if sessions[session_name].get("orchestrator_id"):
            raise ValueError(f"Session '{session_name}' already assigned to another orchestrator")

    # 2. Scan project context
    context_files = _scan_project_context(workdir)

    # 3. Build system prompt
    system_prompt = _generate_orchestrator_prompt(
        context_files=context_files,
        managed_sessions_config=managed_sessions_config,
        goal=goal
    )

    # 4. Create orchestrator object
    orchestrator = {
        "id": orch_id,
        "name": name,
        "provider": provider,
        "provider_session_id": None,  # Will be set on first message
        "managed_sessions": [cfg["session_name"] for cfg in managed_sessions_config],
        "working_directory": workdir,
        "status": "idle",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "context_files_scanned": [f["path"] for f in context_files],
        "system_prompt": system_prompt
    }

    # 5. Save orchestrator
    with _SESSION_LOCK:
        orchestrators = _load_orchestrators(workdir)
        orchestrators[orch_id] = orchestrator
        _save_orchestrators(orchestrators, workdir)

        # 6. Update managed sessions with orchestrator_id and metadata
        for config in managed_sessions_config:
            session_name = config["session_name"]
            sessions[session_name]["orchestrator_id"] = orch_id
            sessions[session_name]["tags"] = config.get("tags", [])
            sessions[session_name]["description"] = config.get("description", "")
        _save_sessions(sessions)

    return orchestrator

def _get_orchestrator(orch_id, workdir=None):
    """Get orchestrator by ID

    Args:
        orch_id: Orchestrator ID
        workdir: Working directory

    Returns:
        dict: Orchestrator object or None
    """
    orchestrators = _load_orchestrators(workdir)
    return orchestrators.get(orch_id)

def _delete_orchestrator(orch_id, workdir=None):
    """Delete orchestrator and unassign managed sessions

    Args:
        orch_id: Orchestrator ID
        workdir: Working directory

    Returns:
        bool: True if deleted, False if not found
    """
    with _SESSION_LOCK:
        orchestrators = _load_orchestrators(workdir)
        if orch_id not in orchestrators:
            return False

        orch = orchestrators[orch_id]

        # Unassign all managed sessions
        sessions = _load_sessions(workdir)
        for session_name in orch["managed_sessions"]:
            if session_name in sessions:
                sessions[session_name]["orchestrator_id"] = None
        _save_sessions(sessions)

        # Delete orchestrator
        del orchestrators[orch_id]
        _save_orchestrators(orchestrators, workdir)

        # TODO: Stop orchestrator if running

        return True

def _update_orchestrator_status(orch_id, status, workdir=None):
    """Update orchestrator status

    Args:
        orch_id: Orchestrator ID
        status: New status (idle, active, paused)
        workdir: Working directory
    """
    with _SESSION_LOCK:
        orchestrators = _load_orchestrators(workdir)
        if orch_id in orchestrators:
            orchestrators[orch_id]["status"] = status
            _save_orchestrators(orchestrators, workdir)
```

#### 2.2 Context Scanning and Prompt Generation

```python
def _scan_project_context(workdir):
    """Scan working directory for project documentation

    Recursively scans for:
    - *.md files (markdown documentation)
    - *.txt files (text documentation)
    - README* files (any extension)
    - ARCHITECTURE* files
    - Any file with common doc patterns

    Args:
        workdir: Directory to scan

    Returns:
        list: List of dicts with {path: str, content: str}

    Notes:
        - Skips binary files
        - Limits file size to 100KB per file
        - Skips hidden directories (.git, .venv, node_modules, etc.)
    """
    import fnmatch

    MAX_FILE_SIZE = 100 * 1024  # 100KB
    SKIP_DIRS = {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache'}
    DOC_PATTERNS = ['*.md', '*.txt', '*.rst', 'README*', 'ARCHITECTURE*', 'DESIGN*']

    context_files = []

    for root, dirs, files in os.walk(workdir):
        # Skip hidden and common exclude directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]

        for filename in files:
            # Check if matches doc pattern
            if not any(fnmatch.fnmatch(filename, pattern) for pattern in DOC_PATTERNS):
                continue

            filepath = os.path.join(root, filename)
            relpath = os.path.relpath(filepath, workdir)

            try:
                # Check file size
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    continue

                # Try to read as text
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    context_files.append({
                        "path": relpath,
                        "content": content
                    })
            except (UnicodeDecodeError, IOError):
                # Skip binary or unreadable files
                continue

    return context_files

def _generate_orchestrator_prompt(context_files, managed_sessions_config, goal=None):
    """Generate orchestrator system prompt

    Args:
        context_files: List of {path, content} dicts from _scan_project_context
        managed_sessions_config: List of session configs with name, description, tags
        goal: Optional user-provided goal

    Returns:
        str: Complete system prompt for orchestrator
    """
    # Build project context section
    context_section = "Project context:\n\n"
    if context_files:
        for file_info in context_files:
            context_section += f"--- {file_info['path']} ---\n{file_info['content']}\n\n"
    else:
        context_section += "(No documentation files found in working directory)\n\n"

    # Build available sessions section
    sessions_section = "Available sessions (use these as tools):\n\n"
    for config in managed_sessions_config:
        name = config["session_name"]
        desc = config.get("description", "")
        tags = config.get("tags", [])
        tags_str = f" [{', '.join(tags)}]" if tags else ""
        sessions_section += f"- {name}{tags_str}: \"{desc}\"\n"

    # Build goal section
    goal_section = ""
    if goal:
        goal_section = f"\nYour objective:\n{goal}\n\n"

    # Assemble complete prompt
    prompt = f"""You are an orchestrator coordinating multiple AI sessions to accomplish tasks.

{context_section}
{sessions_section}
{goal_section}
Your responsibilities:
1. Break down objectives into tasks
2. Assign tasks to appropriate sessions by sending them prompts
3. Monitor session outputs and coordinate handoffs between sessions
4. Only ask the human when you genuinely cannot determine the answer yourself
5. When a session asks a question it cannot answer, relay it to the human in this chat

How to coordinate:
- When you want a session to do something, respond with a JSON action
- Action format: {{"action": "inject_prompt", "target_session": "session_name", "prompt": "what to ask"}}
- To ask human a question: {{"action": "ask_human", "question": "your question"}}
- To wait for more information: {{"action": "wait", "reason": "why waiting"}}

Always respond with valid JSON containing your reasoning and the action to take.
"""

    return prompt
```

#### 2.3 Session Assignment

```python
def _assign_session_to_orchestrator(session_name, orchestrator_id, description, tags, workdir=None):
    """Assign session to orchestrator

    Args:
        session_name: Session to assign
        orchestrator_id: Target orchestrator ID
        description: Session role description
        tags: List of tags
        workdir: Working directory

    Raises:
        ValueError: If session doesn't exist, already assigned, or orchestrator doesn't exist
    """
    with _SESSION_LOCK:
        sessions = _load_sessions(workdir)
        orchestrators = _load_orchestrators(workdir)

        # Validate
        if session_name not in sessions:
            raise ValueError(f"Session '{session_name}' does not exist")
        if orchestrator_id not in orchestrators:
            raise ValueError(f"Orchestrator '{orchestrator_id}' does not exist")

        current_orch = sessions[session_name].get("orchestrator_id")
        if current_orch and current_orch != orchestrator_id:
            raise ValueError(f"Session already assigned to orchestrator '{current_orch}'")

        # Update session
        sessions[session_name]["orchestrator_id"] = orchestrator_id
        sessions[session_name]["description"] = description
        sessions[session_name]["tags"] = tags
        _save_sessions(sessions)

        # Update orchestrator's managed_sessions list
        if session_name not in orchestrators[orchestrator_id]["managed_sessions"]:
            orchestrators[orchestrator_id]["managed_sessions"].append(session_name)
            _save_orchestrators(orchestrators, workdir)

def _unassign_session(session_name, workdir=None):
    """Remove session from its orchestrator

    Args:
        session_name: Session to unassign
        workdir: Working directory
    """
    with _SESSION_LOCK:
        sessions = _load_sessions(workdir)

        if session_name not in sessions:
            return

        orch_id = sessions[session_name].get("orchestrator_id")
        if not orch_id:
            return

        # Remove from session
        sessions[session_name]["orchestrator_id"] = None
        _save_sessions(sessions)

        # Remove from orchestrator's list
        orchestrators = _load_orchestrators(workdir)
        if orch_id in orchestrators:
            managed = orchestrators[orch_id]["managed_sessions"]
            if session_name in managed:
                managed.remove(session_name)
                _save_orchestrators(orchestrators, workdir)
```

---

### Phase 3: API Endpoints (1-2 days)

#### 3.1 Orchestrator Management Routes

```python
@APP.route("/orchestrators", methods=["GET"])
def list_orchestrators():
    """List all orchestrators in current working directory

    Returns:
        JSON: {
            "orchestrators": [
                {
                    "id": "orch_123",
                    "name": "backend-team",
                    "provider": "claude",
                    "status": "active",
                    "managed_sessions": ["dev", "test"],
                    "created_at": "2026-02-01T14:00:00Z"
                },
                ...
            ]
        }
    """
    workdir = request.args.get("workdir") or os.getcwd()
    orchestrators = _load_orchestrators(workdir)

    result = []
    for orch_id, orch in orchestrators.items():
        result.append({
            "id": orch["id"],
            "name": orch["name"],
            "provider": orch["provider"],
            "status": orch["status"],
            "managed_sessions": orch["managed_sessions"],
            "created_at": orch["created_at"]
        })

    return jsonify({"orchestrators": result})

@APP.route("/orchestrators", methods=["POST"])
def create_orchestrator():
    """Create new orchestrator

    Request body:
        {
            "name": "backend-team",
            "provider": "claude",
            "workdir": "/path/to/project",
            "managed_sessions": [
                {
                    "session_name": "dev",
                    "description": "Python backend developer",
                    "tags": ["python", "backend"]
                },
                {
                    "session_name": "test",
                    "description": "Playwright tester",
                    "tags": ["testing", "qa"]
                }
            ],
            "goal": "Build and test login feature"  // optional
        }

    Returns:
        JSON: Created orchestrator object
    """
    data = request.get_json()

    try:
        orch = _create_orchestrator(
            name=data["name"],
            provider=data["provider"],
            workdir=data.get("workdir") or os.getcwd(),
            managed_sessions_config=data["managed_sessions"],
            goal=data.get("goal")
        )
        return jsonify(orch), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError as e:
        return jsonify({"error": f"Missing required field: {e}"}), 400

@APP.route("/orchestrators/<orch_id>", methods=["GET"])
def get_orchestrator(orch_id):
    """Get orchestrator details with managed session statuses

    Returns:
        JSON: {
            "orchestrator": {...},
            "managed_session_statuses": {
                "dev": "idle",
                "test": "active"
            }
        }
    """
    workdir = request.args.get("workdir") or os.getcwd()
    orch = _get_orchestrator(orch_id, workdir)

    if not orch:
        return jsonify({"error": "Orchestrator not found"}), 404

    # Get status of managed sessions
    session_statuses = {}
    for session_name in orch["managed_sessions"]:
        session_statuses[session_name] = _get_session_status(session_name)

    return jsonify({
        "orchestrator": orch,
        "managed_session_statuses": session_statuses
    })

@APP.route("/orchestrators/<orch_id>", methods=["DELETE"])
def delete_orchestrator(orch_id):
    """Delete orchestrator

    Returns:
        JSON: {"deleted": "orch_id"} or {"error": "..."}
    """
    workdir = request.args.get("workdir") or os.getcwd()

    if _delete_orchestrator(orch_id, workdir):
        return jsonify({"deleted": orch_id})
    else:
        return jsonify({"error": "Orchestrator not found"}), 404

@APP.route("/orchestrators/<orch_id>/start", methods=["POST"])
def start_orchestrator(orch_id):
    """Start orchestrator coordination loop

    Request body (optional):
        {
            "initial_message": "Begin working on login feature"
        }

    Returns:
        JSON: {"status": "started"}
    """
    workdir = request.args.get("workdir") or os.getcwd()
    data = request.get_json() or {}

    # TODO: Implement in Phase 4
    # _orchestration_engine.start_orchestrator(orch_id, initial_message=data.get("initial_message"))

    _update_orchestrator_status(orch_id, "active", workdir)
    return jsonify({"status": "started"})

@APP.route("/orchestrators/<orch_id>/pause", methods=["POST"])
def pause_orchestrator(orch_id):
    """Pause orchestrator (stop reacting to events but keep monitoring)

    Returns:
        JSON: {"status": "paused"}
    """
    workdir = request.args.get("workdir") or os.getcwd()

    # TODO: Implement in Phase 4
    # _orchestration_engine.pause_orchestrator(orch_id)

    _update_orchestrator_status(orch_id, "paused", workdir)
    return jsonify({"status": "paused"})

@APP.route("/orchestrators/<orch_id>/resume", methods=["POST"])
def resume_orchestrator(orch_id):
    """Resume paused orchestrator

    Returns:
        JSON: {"status": "active"}
    """
    workdir = request.args.get("workdir") or os.getcwd()

    # TODO: Implement in Phase 4
    # _orchestration_engine.resume_orchestrator(orch_id)

    _update_orchestrator_status(orch_id, "active", workdir)
    return jsonify({"status": "active"})

@APP.route("/orchestrators/<orch_id>/message", methods=["POST"])
def send_orchestrator_message(orch_id):
    """Send message to orchestrator (human responding to question)

    Request body:
        {
            "message": "Use JWT for authentication"
        }

    Returns:
        JSON: Orchestrator's response
    """
    workdir = request.args.get("workdir") or os.getcwd()
    data = request.get_json()

    # TODO: Implement in Phase 4
    # This should:
    # 1. Append user message to orchestrator's history
    # 2. Call orchestrator's AI provider
    # 3. Execute orchestrator's decision (inject prompt, etc.)
    # 4. Return response

    return jsonify({"error": "Not implemented"}), 501
```

#### 3.2 Session Assignment Routes

```python
@APP.route("/sessions/<name>/assign", methods=["POST"])
def assign_session(name):
    """Assign session to orchestrator

    Request body:
        {
            "orchestrator_id": "orch_123",
            "description": "Python backend developer",
            "tags": ["python", "backend"]
        }

    Returns:
        JSON: {"assigned": true} or {"error": "..."}
    """
    data = request.get_json()
    workdir = request.args.get("workdir") or os.getcwd()

    try:
        _assign_session_to_orchestrator(
            session_name=name,
            orchestrator_id=data["orchestrator_id"],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            workdir=workdir
        )
        return jsonify({"assigned": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@APP.route("/sessions/<name>/unassign", methods=["POST"])
def unassign_session(name):
    """Remove session from its orchestrator

    Returns:
        JSON: {"unassigned": true}
    """
    workdir = request.args.get("workdir") or os.getcwd()
    _unassign_session(name, workdir)
    return jsonify({"unassigned": True})

@APP.route("/sessions/<name>", methods=["PATCH"])
def update_session_metadata(name):
    """Update session tags and description

    Request body:
        {
            "tags": ["python", "backend"],
            "description": "Python developer"
        }

    Returns:
        JSON: Updated session object
    """
    data = request.get_json()
    workdir = request.args.get("workdir") or os.getcwd()

    with _SESSION_LOCK:
        sessions = _load_sessions(workdir)

        if name not in sessions:
            return jsonify({"error": "Session not found"}), 404

        if "tags" in data:
            sessions[name]["tags"] = data["tags"]
        if "description" in data:
            sessions[name]["description"] = data["description"]

        _save_sessions(sessions)

        return jsonify(sessions[name])
```

#### 3.3 SSE Enhancement for Session Status

```python
# MODIFY existing function
def _get_session_status(session_name):
    """Get session status (active or idle)

    ENHANCED: Now returns more detail

    Args:
        session_name: Session name

    Returns:
        str: "active" or "idle"
    """
    # Existing logic...
    # Return "active" or "idle"
    pass

# MODIFY existing function
def _sessions_with_status(sessions):
    """Get status map for sessions

    ENHANCED: Add orchestrator_id to status

    Returns:
        dict: {
            "session_name": {
                "status": "active" | "idle",
                "orchestrator_id": "orch_123" | null
            }
        }
    """
    status_map = {}
    for name, record in sessions.items():
        status_map[name] = {
            "status": _get_session_status(name),
            "orchestrator_id": record.get("orchestrator_id")
        }
    return status_map

# MODIFY existing function
@APP.get("/sessions/stream")
def stream_sessions():
    """Stream session updates via SSE

    ENHANCED: Include status in broadcasts

    Payload format:
        {
            "type": "snapshot",
            "sessions": {...},
            "status": {
                "session_name": {
                    "status": "active" | "idle",
                    "orchestrator_id": "orch_123" | null
                }
            }
        }
    """
    def generate():
        q = queue.Queue(maxsize=100)
        _SESSION_SUBSCRIBERS.add(q)
        try:
            snapshot = _build_sessions_snapshot()
            yield f"data: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"
            while True:
                payload = q.get()
                yield f"data: {json.dumps({'type': 'snapshot', **payload})}\n\n"
        finally:
            _SESSION_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")
```

---

### Phase 4: Orchestration Engine (3-4 days)

**Most complex phase - handles autonomous coordination**

#### 4.1 Orchestration Engine Class

```python
class OrchestrationEngine:
    """Manages orchestrator lifecycle and coordination"""

    def __init__(self):
        self.active_orchestrators = {}  # {orch_id: {"thread": Thread, "stop_event": Event}}
        self.session_event_queue = queue.Queue()
        self.lock = threading.Lock()

    def start(self):
        """Start global orchestration monitoring thread"""
        threading.Thread(target=self._monitor_session_events, daemon=True).start()

    def _monitor_session_events(self):
        """Monitor /sessions/stream for status changes

        Listens to session status broadcasts and triggers orchestrators
        when their managed sessions go from active -> idle
        """
        # Subscribe to session updates
        event_queue = queue.Queue(maxsize=100)
        _SESSION_SUBSCRIBERS.add(event_queue)

        previous_statuses = {}  # {session_name: "active" | "idle"}

        try:
            while True:
                try:
                    payload = event_queue.get(timeout=1)
                    status_map = payload.get("status", {})

                    # Detect status changes
                    for session_name, info in status_map.items():
                        current_status = info.get("status")
                        previous_status = previous_statuses.get(session_name)

                        # Trigger on active -> idle transition
                        if previous_status == "active" and current_status == "idle":
                            orchestrator_id = info.get("orchestrator_id")
                            if orchestrator_id:
                                # Session completed work, notify orchestrator
                                self._handle_session_completion(orchestrator_id, session_name)

                        previous_statuses[session_name] = current_status

                except queue.Empty:
                    continue
        finally:
            _SESSION_SUBSCRIBERS.discard(event_queue)

    def start_orchestrator(self, orch_id, workdir, initial_message=None):
        """Start orchestrator

        Args:
            orch_id: Orchestrator ID
            workdir: Working directory
            initial_message: Optional first message from user
        """
        with self.lock:
            if orch_id in self.active_orchestrators:
                return  # Already running

            # Mark as active
            _update_orchestrator_status(orch_id, "active", workdir)

            # If initial message provided, process it
            if initial_message:
                self._send_message_to_orchestrator(orch_id, workdir, initial_message, source="human")

    def pause_orchestrator(self, orch_id, workdir):
        """Pause orchestrator (stop reacting but keep monitoring)"""
        _update_orchestrator_status(orch_id, "paused", workdir)

    def resume_orchestrator(self, orch_id, workdir):
        """Resume orchestrator"""
        _update_orchestrator_status(orch_id, "active", workdir)

    def _handle_session_completion(self, orch_id, session_name):
        """Called when a managed session goes idle

        Args:
            orch_id: Orchestrator that manages this session
            session_name: Session that just completed
        """
        # Load orchestrator
        orch = _get_orchestrator(orch_id)
        if not orch or orch["status"] != "active":
            return  # Orchestrator not active

        # Get orchestrator's decision on what to do next
        workdir = orch["working_directory"]
        self._orchestrate_next_action(orch_id, workdir, session_name)

    def _orchestrate_next_action(self, orch_id, workdir, completed_session):
        """Main orchestration logic

        Args:
            orch_id: Orchestrator ID
            workdir: Working directory
            completed_session: Session that just finished work
        """
        orch = _get_orchestrator(orch_id, workdir)

        # 1. Read recent history from completed session
        session_summary = self._get_session_recent_activity(completed_session, workdir)

        # 2. Load orchestrator's conversation history
        orch_history = self._load_orchestrator_history(orch_id, workdir)

        # 3. Build context for orchestrator's AI
        context = f"""Session '{completed_session}' has completed work.

Recent activity in {completed_session}:
{session_summary}

What should happen next? Respond with JSON action.
"""

        # 4. Call orchestrator's AI provider to get decision
        decision = self._get_orchestrator_decision(orch, orch_history, context, workdir)

        # 5. Execute the decision
        self._execute_orchestrator_decision(orch_id, workdir, decision)

    def _get_session_recent_activity(self, session_name, workdir):
        """Get summary of recent session activity

        Args:
            session_name: Session name
            workdir: Working directory

        Returns:
            str: Summary of last few messages
        """
        history = _get_history_for_name(session_name)
        if not history or not history.get("messages"):
            return "(No recent activity)"

        # Get last 5 messages
        recent_messages = history["messages"][-5:]

        summary = []
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            text = msg.get("text", "")[:200]  # Truncate long messages
            summary.append(f"{role}: {text}")

        return "\n".join(summary)

    def _load_orchestrator_history(self, orch_id, workdir):
        """Load orchestrator's conversation history

        Returns:
            list: Messages in orchestrator's conversation
        """
        history_data = _load_history(workdir)

        # Find orchestrator's history entry
        for session_id, entry in history_data.items():
            if entry.get("orchestrator_id") == orch_id:
                return entry.get("messages", [])

        return []

    def _get_orchestrator_decision(self, orch, history, context, workdir):
        """Call orchestrator's AI to decide next action

        Args:
            orch: Orchestrator object
            history: Orchestrator's conversation history
            context: Current situation context
            workdir: Working directory

        Returns:
            dict: Decision object like:
                {
                    "action": "inject_prompt" | "ask_human" | "wait",
                    "target_session": "session_name",  # if inject_prompt
                    "prompt": "message to send",        # if inject_prompt
                    "question": "question for human",   # if ask_human
                    "reason": "why waiting"             # if wait
                }
        """
        provider = orch["provider"]
        system_prompt = orch["system_prompt"]

        # Build messages for provider
        messages = [
            {"role": "system", "content": system_prompt}
        ]

        # Add history
        for msg in history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("text", "")
            })

        # Add current context
        messages.append({
            "role": "user",
            "content": context
        })

        # Call provider based on type
        if provider == "claude":
            response = self._call_claude_for_decision(messages, orch, workdir)
        elif provider == "codex":
            response = self._call_codex_for_decision(messages, orch, workdir)
        # ... other providers

        # Parse JSON decision from response
        try:
            decision = json.loads(response)
            return decision
        except json.JSONDecodeError:
            # Fallback: wait if can't parse
            return {"action": "wait", "reason": "Could not parse orchestrator response"}

    def _call_claude_for_decision(self, messages, orch, workdir):
        """Call Claude to get orchestrator decision

        Uses existing _run_claude_exec logic
        """
        # Convert messages to prompt
        prompt = messages[-1]["content"]  # Latest message is the context

        # Build history for --resume
        history_messages = messages[1:-1]  # Skip system and current

        # Call Claude
        result = _run_claude_exec(
            prompt=prompt,
            history_messages=history_messages,
            config=_load_client_config(),
            cwd=workdir,
            resume_session_id=orch.get("provider_session_id")
        )

        # Update orchestrator's provider session ID
        if result.get("session_id"):
            with _SESSION_LOCK:
                orchestrators = _load_orchestrators(workdir)
                orchestrators[orch["id"]]["provider_session_id"] = result["session_id"]
                _save_orchestrators(orchestrators, workdir)

        # Extract response text
        conversation = result.get("conversation", {})
        messages = conversation.get("messages", [])
        if messages:
            return messages[-1].get("text", "")

        return ""

    def _execute_orchestrator_decision(self, orch_id, workdir, decision):
        """Execute orchestrator's decision

        Args:
            orch_id: Orchestrator ID
            workdir: Working directory
            decision: Decision dict from _get_orchestrator_decision
        """
        action = decision.get("action")

        if action == "inject_prompt":
            # Send prompt to target session
            target_session = decision.get("target_session")
            prompt = decision.get("prompt")

            if target_session and prompt:
                self._inject_prompt_to_session(target_session, prompt, orch_id, workdir)

        elif action == "ask_human":
            # Post question to orchestrator's chat for human response
            question = decision.get("question")
            if question:
                self._post_question_to_orchestrator_chat(orch_id, workdir, question)

        elif action == "wait":
            # Do nothing, just log reasoning
            reason = decision.get("reason", "Waiting for more information")
            logging.info(f"Orchestrator {orch_id} waiting: {reason}")

    def _inject_prompt_to_session(self, session_name, prompt, orchestrator_id, workdir):
        """Inject prompt into session (simulate user input)

        Args:
            session_name: Target session
            prompt: Message to send
            orchestrator_id: Source orchestrator
            workdir: Working directory
        """
        # Create message with orchestrator source
        message_data = {
            "prompt": prompt,
            "session_name": session_name,
            "workdir": workdir,
            "_orchestrator_source": orchestrator_id  # Internal flag
        }

        # Call existing /exec endpoint logic
        # This will:
        # 1. Load session
        # 2. Execute prompt with provider
        # 3. Save to history with orchestrator metadata

        # Use internal function (extract from /exec route)
        result = _execute_prompt_for_session(message_data)

        # Log the injection
        logging.info(f"Orchestrator {orchestrator_id} injected to {session_name}: {prompt[:50]}...")

    def _post_question_to_orchestrator_chat(self, orch_id, workdir, question):
        """Post question from orchestrator to its chat for human to answer

        Args:
            orch_id: Orchestrator ID
            workdir: Working directory
            question: Question to ask human
        """
        # Append assistant message to orchestrator's history
        message = _create_message(
            role="assistant",
            text=question,
            source=f"orchestrator:{orch_id}"
        )

        # Save to orchestrator's history
        # (Implementation depends on history structure)
        # TODO: Append to orchestrator's conversation in history file

        # Broadcast update so UI shows the question
        # (Use SSE or similar mechanism)

    def _send_message_to_orchestrator(self, orch_id, workdir, message, source="human"):
        """Send message to orchestrator (from human)

        Args:
            orch_id: Orchestrator ID
            workdir: Working directory
            message: Message text
            source: Message source
        """
        orch = _get_orchestrator(orch_id, workdir)

        # Load history
        history = self._load_orchestrator_history(orch_id, workdir)

        # Build context (similar to _orchestrate_next_action)
        context = message

        # Get decision
        decision = self._get_orchestrator_decision(orch, history, context, workdir)

        # Execute
        self._execute_orchestrator_decision(orch_id, workdir, decision)

# Global instance
_orchestration_engine = OrchestrationEngine()

# Start on app startup
threading.Thread(target=_orchestration_engine.start, daemon=True).start()
```

#### 4.2 Helper Functions

```python
def _execute_prompt_for_session(message_data):
    """Execute prompt for a session (used by orchestrator injection)

    Args:
        message_data: Dict with prompt, session_name, workdir, _orchestrator_source

    Returns:
        dict: Execution result
    """
    # Extract from existing /exec route logic
    # This should:
    # 1. Load session
    # 2. Determine provider
    # 3. Call provider with prompt
    # 4. Save conversation with orchestrator metadata if _orchestrator_source present
    # 5. Return result
    pass

def _create_message(role, text, source="human"):
    """Create message with metadata (defined in Phase 1.3)"""
    return {
        "role": role,
        "text": text,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": source
    }
```

---

### Phase 5: Frontend UI (2-3 days)

#### 5.1 Navigation Enhancement

**File:** `templates/chat.html` (or base template)

**Add "Orchestrators" section:**
```html
<nav>
  <!-- Existing navigation -->
  <a href="/chat">Sessions</a>
  <a href="/orchestrators">Orchestrators</a>  <!-- NEW -->
  <a href="/tasks">Tasks</a>
  <a href="/config">Config</a>
</nav>
```

#### 5.2 Orchestrator List Page

**New file:** `templates/orchestrators.html`

```html
<!DOCTYPE html>
<html>
<head>
  <title>Orchestrators - Bil-dir</title>
  <style>
    /* Reuse existing styles from chat.html */
    .orchestrator-card {
      border: 1px solid #444;
      padding: 1rem;
      margin: 0.5rem 0;
      border-radius: 4px;
    }
    .orchestrator-card.active {
      border-left: 4px solid #0f0;
    }
    .orchestrator-card.paused {
      border-left: 4px solid #ff0;
    }
    .orchestrator-card.idle {
      border-left: 4px solid #666;
    }
    .managed-sessions {
      display: flex;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }
    .session-badge {
      padding: 0.2rem 0.5rem;
      background: #333;
      border-radius: 3px;
      font-size: 0.85rem;
    }
  </style>
</head>
<body>
  <nav>
    <a href="/chat">Sessions</a>
    <a href="/orchestrators" class="active">Orchestrators</a>
    <a href="/tasks">Tasks</a>
    <a href="/config">Config</a>
  </nav>

  <main>
    <div class="header">
      <h1>Orchestrators</h1>
      <button onclick="showCreateModal()">+ New Orchestrator</button>
    </div>

    <div id="orchestrator-list"></div>
  </main>

  <!-- Create Orchestrator Modal -->
  <dialog id="create-modal">
    <form id="create-form">
      <h2>Create Orchestrator</h2>

      <label>Name:</label>
      <input type="text" name="name" required>

      <label>Provider:</label>
      <select name="provider" required>
        <option value="claude">Claude</option>
        <option value="codex">Codex</option>
        <option value="copilot">Copilot</option>
        <option value="gemini">Gemini</option>
      </select>

      <label>Working Directory:</label>
      <input type="text" name="workdir" placeholder="Current directory">

      <label>Goal (optional):</label>
      <textarea name="goal" placeholder="What should this orchestrator accomplish?"></textarea>

      <h3>Managed Sessions</h3>
      <div id="session-selection"></div>

      <div class="actions">
        <button type="submit">Create</button>
        <button type="button" onclick="closeCreateModal()">Cancel</button>
      </div>
    </form>
  </dialog>

  <script>
    let sessions = [];
    let orchestrators = [];

    async function loadData() {
      // Load sessions
      const sessionsResp = await fetch('/sessions');
      const sessionsData = await sessionsResp.json();
      sessions = sessionsData.sessions || [];

      // Load orchestrators
      const orchResp = await fetch('/orchestrators');
      const orchData = await orchResp.json();
      orchestrators = orchData.orchestrators || [];

      renderOrchestrators();
    }

    function renderOrchestrators() {
      const container = document.getElementById('orchestrator-list');

      if (orchestrators.length === 0) {
        container.innerHTML = '<p>No orchestrators yet. Create one to coordinate multiple sessions.</p>';
        return;
      }

      container.innerHTML = orchestrators.map(orch => `
        <div class="orchestrator-card ${orch.status}">
          <div class="header">
            <h3>${orch.name}</h3>
            <span class="status">${orch.status}</span>
          </div>

          <div class="info">
            <span>Provider: ${orch.provider}</span>
            <span>Created: ${new Date(orch.created_at).toLocaleString()}</span>
          </div>

          <div class="managed-sessions">
            ${orch.managed_sessions.map(s => `<span class="session-badge">${s}</span>`).join('')}
          </div>

          <div class="actions">
            <button onclick="openOrchestrator('${orch.id}')">Open</button>
            ${orch.status === 'idle' ? `<button onclick="startOrchestrator('${orch.id}')">Start</button>` : ''}
            ${orch.status === 'active' ? `<button onclick="pauseOrchestrator('${orch.id}')">Pause</button>` : ''}
            ${orch.status === 'paused' ? `<button onclick="resumeOrchestrator('${orch.id}')">Resume</button>` : ''}
            <button onclick="deleteOrchestrator('${orch.id}')">Delete</button>
          </div>
        </div>
      `).join('');
    }

    function showCreateModal() {
      // Populate session selection
      const container = document.getElementById('session-selection');
      container.innerHTML = sessions.map(session => `
        <div class="session-option">
          <input type="checkbox" id="session-${session.name}" name="sessions" value="${session.name}">
          <label for="session-${session.name}">${session.name} (${session.provider})</label>

          <div class="session-metadata" id="meta-${session.name}" style="display:none; margin-left: 2rem;">
            <label>What is this session for?</label>
            <input type="text" placeholder="e.g., Python backend development" data-session="${session.name}" data-field="description" required>

            <label>Tags (comma-separated):</label>
            <input type="text" placeholder="e.g., python, backend" data-session="${session.name}" data-field="tags">
          </div>
        </div>
      `).join('');

      // Show/hide metadata inputs when checkbox changes
      container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const sessionName = e.target.value;
          const metaDiv = document.getElementById(`meta-${sessionName}`);
          metaDiv.style.display = e.target.checked ? 'block' : 'none';
        });
      });

      document.getElementById('create-modal').showModal();
    }

    function closeCreateModal() {
      document.getElementById('create-modal').close();
    }

    async function createOrchestrator(e) {
      e.preventDefault();

      const form = e.target;
      const formData = new FormData(form);

      // Collect managed sessions with metadata
      const managedSessions = [];
      form.querySelectorAll('input[name="sessions"]:checked').forEach(cb => {
        const sessionName = cb.value;
        const description = form.querySelector(`input[data-session="${sessionName}"][data-field="description"]`).value;
        const tagsStr = form.querySelector(`input[data-session="${sessionName}"][data-field="tags"]`).value;
        const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()) : [];

        managedSessions.push({
          session_name: sessionName,
          description: description,
          tags: tags
        });
      });

      if (managedSessions.length === 0) {
        alert('Please select at least one session');
        return;
      }

      const data = {
        name: formData.get('name'),
        provider: formData.get('provider'),
        workdir: formData.get('workdir') || undefined,
        goal: formData.get('goal') || undefined,
        managed_sessions: managedSessions
      };

      const resp = await fetch('/orchestrators', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
      });

      if (resp.ok) {
        closeCreateModal();
        loadData();
      } else {
        const error = await resp.json();
        alert(`Error: ${error.error}`);
      }
    }

    function openOrchestrator(orchId) {
      window.location.href = `/orchestrators/${orchId}/chat`;
    }

    async function startOrchestrator(orchId) {
      await fetch(`/orchestrators/${orchId}/start`, {method: 'POST'});
      loadData();
    }

    async function pauseOrchestrator(orchId) {
      await fetch(`/orchestrators/${orchId}/pause`, {method: 'POST'});
      loadData();
    }

    async function resumeOrchestrator(orchId) {
      await fetch(`/orchestrators/${orchId}/resume`, {method: 'POST'});
      loadData();
    }

    async function deleteOrchestrator(orchId) {
      if (!confirm('Delete this orchestrator? Managed sessions will be unassigned.')) return;

      await fetch(`/orchestrators/${orchId}`, {method: 'DELETE'});
      loadData();
    }

    document.getElementById('create-form').addEventListener('submit', createOrchestrator);

    loadData();

    // Subscribe to SSE updates
    const source = new EventSource('/sessions/stream');
    source.onmessage = () => loadData();
  </script>
</body>
</html>
```

#### 5.3 Orchestrator Chat UI

**New file:** `templates/orchestrator_chat.html`

Similar to `chat.html` but:
- Shows orchestrator conversation (human <-> orchestrator)
- Displays live feed of actions (e.g., "Injected to dev: implement login")
- Shows managed session statuses
- Human can respond to orchestrator's questions here

```html
<!-- Similar structure to chat.html -->
<!-- Key differences: -->
<!-- 1. Title shows orchestrator name -->
<!-- 2. Sidebar shows managed sessions with status lights -->
<!-- 3. Messages include orchestrator actions (injections, questions) -->
<!-- 4. User sends messages to orchestrator, not to sessions -->
```

#### 5.4 Session Menu Enhancement

**File:** `templates/chat.html`

**Add "Assign to Orchestrator" option in session dropdown:**

```javascript
function showSessionMenu(sessionName) {
  const menu = `
    <div class="context-menu">
      <button onclick="renameSession('${sessionName}')">Rename</button>
      <button onclick="deleteSession('${sessionName}')">Delete</button>
      <button onclick="showAssignModal('${sessionName}')">Assign to Orchestrator</button>  <!-- NEW -->
    </div>
  `;
  // ... show menu
}

function showAssignModal(sessionName) {
  // Fetch orchestrators
  // Show modal with:
  // - Select orchestrator dropdown
  // - Description input (what is this session for?)
  // - Tags input
  // - Assign button
}
```

---

## Technical Decisions

### 1. Orchestrator Decision Format: Tool-Based with JSON Response

**Implementation:** Orchestrator responds with structured JSON:
```json
{
  "reasoning": "Dev completed feature, now testing needed",
  "action": "inject_prompt",
  "target_session": "test",
  "prompt": "Run Playwright tests on login feature"
}
```

**Why:**
- Reliable parsing (JSON.parse)
- Clear action types
- Easy to extend
- Doesn't require tool use API support from all providers

**Alternative considered:** Tool calling API (only if provider supports it natively)

### 2. Threading Model: Single Global Thread

**Implementation:** One `OrchestrationEngine` thread monitors all orchestrators

**Why:**
- Simpler architecture
- Less resource overhead
- Orchestrators are event-driven (not CPU-intensive)
- Easier to debug and maintain

**Trade-off:** One orchestrator blocking could delay others (mitigated by async processing)

### 3. Session Notification: SSE via Existing `/sessions/stream`

**Implementation:** Enhance existing SSE endpoint to include session status

**Why:**
- Leverages existing infrastructure
- No new pub/sub system needed
- Real-time (not polling)
- Frontend already uses it

**Changes needed:** Add `status: "active"|"idle"` to session broadcast payload

### 4. Context Scanning: Scan All Docs, No Hardcoding

**Implementation:** Recursively scan for `*.md`, `*.txt`, `README*`, etc.

**Why:**
- Flexible (works with any project structure)
- User doesn't need to configure
- Finds all relevant documentation automatically

**Safeguards:**
- Skip `.git`, `node_modules`, `.venv`
- Limit file size (100KB per file)
- Skip binary files

### 5. History Storage: Unified Format

**Implementation:** Orchestrators store history in same `.codex_history.json` as sessions

**Why:**
- Single parser
- Easy to query
- Consistent format
- Minimal code changes

**Distinction:** Add `orchestrator_id` field to identify orchestrator vs session history

---

## Backwards Compatibility

### Data Format Changes (All Non-Breaking)

**Session model:**
- ✅ Add optional fields (`orchestrator_id`, `tags`, `description`)
- ✅ Old sessions without fields continue working
- ✅ Code uses `.get()` with defaults

**Message format:**
- ✅ Add optional fields (`timestamp`, `source`)
- ✅ Old messages without fields display correctly
- ✅ Templates only access `.role` and `.text`

**SSE payload:**
- ✅ Add `status` field to existing broadcast
- ✅ Frontend extracts only what it needs
- ✅ Extra fields ignored

### New Functionality (Purely Additive)

- ✅ New files (`.codex_orchestrators.json`)
- ✅ New API routes (`/orchestrators/*`)
- ✅ New backend functions
- ✅ New UI pages
- ✅ New background thread

### No Breaking Changes

- ❌ No modifications to existing session execution
- ❌ No changes to provider calling logic
- ❌ No removal of existing features
- ❌ No breaking API changes

### Migration Strategy

**Existing data works as-is:**
1. Old sessions load normally
2. Old messages display correctly
3. Existing workflows unchanged
4. Orchestrators are opt-in

**Testing each phase:**
1. Phase 1: Verify old sessions still load/save
2. Phase 3: Test existing routes still work
3. Phase 4: Run orchestrator while using sessions manually

---

## Testing Requirements

### Unit Tests

**Phase 1: Data Model**
- ✅ Test session with new fields saves/loads correctly
- ✅ Test session without new fields still works
- ✅ Test message creation with timestamp/source
- ✅ Test orchestrator creation/loading/saving

**Phase 2: Backend Functions**
- ✅ Test `_scan_project_context` finds all docs
- ✅ Test `_generate_orchestrator_prompt` builds correct prompt
- ✅ Test `_assign_session_to_orchestrator` validation
- ✅ Test session assignment prevents double-assignment

**Phase 4: Orchestration Engine**
- ✅ Test session completion detection
- ✅ Test orchestrator decision parsing
- ✅ Test prompt injection to sessions
- ✅ Test human-in-loop question routing

### Integration Tests

**End-to-end workflows:**
1. Create orchestrator with 2 sessions
2. Start orchestrator with goal
3. Verify it injects to first session
4. Simulate session completion
5. Verify orchestrator reacts and injects to second session
6. Verify human-in-loop question flow

**Backwards compatibility:**
1. Create old-style session (no new fields)
2. Send messages (without timestamp/source)
3. Verify everything works
4. Create orchestrator
5. Verify old session can be assigned

### Manual Testing Checklist

- [ ] Create orchestrator via UI
- [ ] View orchestrator list
- [ ] Start/pause/resume orchestrator
- [ ] Send message to orchestrator
- [ ] Verify orchestrator injects to session
- [ ] Verify session completion triggers orchestrator
- [ ] Verify human-in-loop question appears in orchestrator chat
- [ ] Delete orchestrator, verify sessions unassigned
- [ ] Assign session via session menu
- [ ] Use session manually while orchestrator is running (no conflicts)

---

## Implementation Timeline

**Phase 1: Data Model (1-2 days)**
- Day 1: Session/orchestrator storage, message format
- Day 2: Testing, validation

**Phase 2: Backend Functions (2-3 days)**
- Day 1: CRUD operations
- Day 2: Context scanning, prompt generation
- Day 3: Session assignment, testing

**Phase 3: API Endpoints (1-2 days)**
- Day 1: Orchestrator routes
- Day 2: Session assignment routes, SSE enhancement

**Phase 4: Orchestration Engine (3-4 days)**
- Day 1-2: Engine class, session monitoring
- Day 3: Decision making, prompt injection
- Day 4: Testing, debugging

**Phase 5: Frontend UI (2-3 days)**
- Day 1: Orchestrator list page, create modal
- Day 2: Orchestrator chat UI
- Day 3: Session menu, polish

**Total: 10-14 days**

---

## Success Criteria

### Functional Requirements Met

- ✅ User can create orchestrator with multiple sessions
- ✅ Orchestrator reads project documentation automatically
- ✅ Orchestrator monitors session status changes
- ✅ Orchestrator injects prompts to sessions when appropriate
- ✅ Orchestrator asks human when stuck
- ✅ Human can respond via orchestrator chat
- ✅ Sessions can be assigned/unassigned
- ✅ Start/pause/resume orchestrator controls work

### Non-Functional Requirements Met

- ✅ Backwards compatible (existing sessions/workflows unchanged)
- ✅ Event-driven (no polling overhead)
- ✅ Performant (no noticeable impact on existing features)
- ✅ Maintainable (follows existing code patterns)
- ✅ Documented (this requirements doc)

### User Experience Goals

- ✅ Intuitive UI (similar to sessions/tasks)
- ✅ Clear feedback (know what orchestrator is doing)
- ✅ Transparent (can see orchestrator actions in session history)
- ✅ Recoverable (can pause/resume, delete without breaking sessions)

---

## Future Enhancements (Out of Scope for Initial Release)

- **Multi-orchestrator coordination:** Orchestrators coordinating with each other
- **Orchestrator templates:** Pre-defined orchestrator patterns (dev+test, CI/CD, etc.)
- **Workflow visualization:** Graph view of session dependencies and handoffs
- **Orchestrator analytics:** Success rate, average time to completion
- **Dynamic session creation:** Orchestrator creates new sessions as needed
- **External triggers:** Start orchestrator via webhook, schedule, file watch
- **Orchestrator sharing:** Export/import orchestrator configs

---

## References

- Existing session architecture: `app.py` lines 2500-2700
- SSE implementation: `/sessions/stream`, `/tasks/stream`
- History format: `.codex_history.json` structure
- Message format: Current `{role, text}` pattern
- Git commit: "Initial commit: Flask orchestrator with cross-provider context"

---

## Appendix: Example Orchestrator Session

**Scenario:** Build and test login feature

```
User -> Orchestrator: "Implement login with JWT authentication"

Orchestrator reads:
  - README.md (tech stack: Flask + React)
  - requirements.md (login requirements)

Orchestrator decides:
  Action: inject_prompt
  Target: dev_session
  Prompt: "Implement JWT login per requirements.md. Use Flask-JWT-Extended."

[dev_session works... goes idle]

Orchestrator sees dev completed:
  - Created src/auth.py
  - Added login endpoint

Orchestrator decides:
  Action: inject_prompt
  Target: test_session
  Prompt: "Test login endpoint in src/auth.py. Verify JWT token generation."

[test_session works... goes idle]

Orchestrator sees test results:
  - Test failed: "Token missing 'exp' claim"

Orchestrator decides:
  Action: inject_prompt
  Target: dev_session
  Prompt: "Fix: Add 'exp' claim to JWT token. Test expects expiration."

[dev_session fixes... goes idle]

Orchestrator decides:
  Action: inject_prompt
  Target: test_session
  Prompt: "Re-run login tests to verify fix."

[test_session works... goes idle]

Orchestrator sees test results:
  - All tests passing

Orchestrator decides:
  Action: ask_human
  Question: "Login feature complete and tested. Deploy to staging?"

User -> Orchestrator: "Yes, deploy"

Orchestrator decides:
  Action: inject_prompt
  Target: deploy_session
  Prompt: "Deploy login feature to staging environment."
```

---

**End of Requirements Document**
