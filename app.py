import os
import subprocess
import html
import json
import pathlib
import shutil
import threading
import queue
import time
import uuid
import datetime
from collections import deque

from flask import Flask, jsonify, request, Response, render_template

from utils.config import (
    logger,
    APP_START_TIME,
    DEFAULT_CWD,
    SESSION_STORE_PATH,
    HISTORY_STORE_PATH,
    CLIENT_CONFIG_PATH,
    LOG_STORE_PATH,
    MCP_JSON_PATH,
    PROVIDER_CONFIG_PATH,
    TASK_STORE_PATH,
    ORCH_STORE_PATH,
    CONTEXT_DIR,
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
    PROVIDER_ORDER,
    DEFAULT_ORCH_BASE_PROMPT,
    DEFAULT_ORCH_WORKER_PROMPT,
    _get_provider_config,
    _get_orchestrator_base_prompt,
    _get_orchestrator_worker_prompt,
    _full_permissions_enabled,
    _get_sandbox_mode,
    _get_provider_model_info,
    _get_codex_home,
    _load_client_config,
    _save_client_config,
)
from utils.validation import (
    _validate_name,
    _validate_provider,
    _require_json_body,
    _validate_schedule,
)
from providers.base import _filter_debug_messages, _enqueue_output
from providers.codex import (
    _run_codex_exec,
    _run_codex_exec_stream,
    _resolve_codex_path,
    _extract_session_id,
    _extract_codex_assistant_output,
    _build_codex_args,
)
from providers.copilot import (
    _run_copilot_exec,
    _resolve_copilot_path,
    _strip_copilot_footer,
    _is_copilot_footer_line,
)
from providers.gemini import (
    _run_gemini_exec,
    _run_gemini_exec_stream,
    _resolve_gemini_path,
    _ensure_gemini_policy,
    _gca_available,
    _get_gemini_api_key_from_settings,
)
from providers.claude import (
    _run_claude_exec,
    _run_claude_exec_stream,
    _resolve_claude_path,
    _get_latest_claude_session_id,
    _wait_for_claude_session_id,
    _is_uuid,
    _clean_claude_output,
)
from core.state import (
    _SESSION_LOCK,
    _JOB_LOCK,
    _TASK_LOCK,
    _ORCH_LOCK,
    _PENDING_LOCK,
    _SESSION_STATUS,
    _JOBS,
    _SESSION_SUBSCRIBERS,
    _TASK_SUBSCRIBERS,
    _MASTER_SUBSCRIBERS,
    _SESSION_VIEWERS,
    _ORCH_STATE,
    _PENDING_PROMPTS,
)
from core.mcp_manager import (
    _get_mcp_servers,
    _load_mcp_json,
    _write_mcp_json_file,
    _write_codex_mcp_config,
    _toml_escape,
)
from core.session_manager import (
    _normalize_session_record,
    _normalize_sessions,
    _load_sessions,
    _save_sessions,
    _get_session_id_for_name,
    _get_session_provider_for_name,
    _get_session_workdir,
    _get_session_status,
    _session_has_active_job,
    _set_session_status,
    _set_session_name,
    _set_session_provider,
    _ensure_session_id,
    _sessions_with_status,
    _build_session_list,
    _build_sessions_snapshot,
    _touch_session,
    _broadcast_sessions_snapshot,
)
from core.task_manager import (
    _normalize_task,
    _load_tasks,
    _save_tasks,
    _ensure_task_history,
    _format_task_run_header,
    _build_task_history_text,
)
from core.orchestrator_manager import (
    _normalize_orchestrator,
    _load_orchestrators,
    _save_orchestrators,
    _build_orchestrator_list,
    _append_orchestrator_history,
    _build_orchestrator_history_text,
    _infer_worker_role,
    _build_worker_kickoff_prompt,
    _extract_json_action,
)

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
APP = Flask(__name__, template_folder=_TEMPLATE_DIR)
APP.config["TEMPLATES_AUTO_RELOAD"] = True
APP.jinja_env.auto_reload = True

# API Error Codes - Centralized definitions for consistent error handling
# Validation Errors
ERR_INVALID_INPUT = "INVALID_INPUT"
ERR_INVALID_PROMPT = "INVALID_PROMPT"
ERR_INVALID_TIMEOUT = "INVALID_TIMEOUT"
ERR_INVALID_PROVIDER = "INVALID_PROVIDER"
ERR_INVALID_SCHEDULE = "INVALID_SCHEDULE"
ERR_MISSING_SESSION_NAME = "MISSING_SESSION_NAME"
ERR_MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

# Resource Not Found Errors
ERR_NOT_FOUND = "NOT_FOUND"
ERR_TASK_NOT_FOUND = "TASK_NOT_FOUND"
ERR_ORCHESTRATOR_NOT_FOUND = "ORCHESTRATOR_NOT_FOUND"
ERR_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"

# Provider/CLI Errors
ERR_UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
ERR_CLI_NOT_FOUND = "CLI_NOT_FOUND"
ERR_NPX_NOT_FOUND = "NPX_NOT_FOUND"

# Operation Errors
ERR_TIMEOUT = "TIMEOUT"
ERR_CONFLICT = "CONFLICT"
ERR_OPERATION_FAILED = "OPERATION_FAILED"


def _format_duration(seconds):
    seconds = int(max(0, seconds))
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins:
        parts.append(f"{mins}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _safe_cwd(candidate):
    if candidate:
        return os.path.abspath(candidate)
    config = _get_provider_config()
    default_cwd = (config.get("default_workdir") or "").strip() if isinstance(config, dict) else ""
    return os.path.abspath(default_cwd or DEFAULT_CWD)


def _error_response(message, code=None, details=None, status=400):
    """
    Standard error response format for all API endpoints.

    Args:
        message: Human-readable error message
        code: Optional error code (e.g., "INVALID_INPUT", "NOT_FOUND")
        details: Optional additional error details (dict)
        status: HTTP status code (default 400)

    Returns:
        Tuple of (jsonify response, status code)
    """
    payload = {"error": message}
    if code:
        payload["code"] = code
    if details:
        payload["details"] = details
    return jsonify(payload), status


def _resolve_npx_path():
    candidates = [
        shutil.which("npx"),
        shutil.which("npx.cmd"),
        r"C:\Program Files\nodejs\npx.cmd",
        r"C:\Program Files\nodejs\npx",
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "npx.cmd"),
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "npx"),
        "/usr/local/bin/npx",
        "/opt/homebrew/bin/npx",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _provider_path_status(config):
    return {
        "codex": bool(_resolve_codex_path()),
        "copilot": bool(_resolve_copilot_path(config)),
        "gemini": bool(_resolve_gemini_path(config)),
        "claude": bool(_resolve_claude_path(config)),
    }


def _get_available_providers(config):
    status = _provider_path_status(config)
    available = [p for p in PROVIDER_ORDER if status.get(p)]
    return available

 


def _get_gmail_status_message(reason):
    if reason == "missing_credentials":
        return "Gmail credentials not found"
    if reason == "missing_refresh_token":
        return "Missing refresh token - authentication required"
    if reason == "invalid_credentials":
        return "Invalid Gmail credentials file"
    if reason == "read_error":
        return "Unable to read Gmail credentials"
    return "Authenticated"


def _gmail_auth_status():
    creds_path = pathlib.Path.home() / ".gmail-mcp" / "credentials.json"
    if not creds_path.exists():
        reason = "missing_credentials"
        return {
            "authenticated": False,
            "status": "warning",
            "reason": reason,
            "message": _get_gmail_status_message(reason),
        }
    try:
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Invalid Gmail credentials file: {e}")
        reason = "invalid_credentials"
        return {
            "authenticated": False,
            "status": "warning",
            "reason": reason,
            "message": _get_gmail_status_message(reason),
        }
    except OSError as e:
        logger.error(f"Cannot read Gmail credentials: {e}")
        reason = "read_error"
        return {
            "authenticated": False,
            "status": "warning",
            "reason": reason,
            "message": _get_gmail_status_message(reason),
        }
    refresh_token = data.get("refresh_token") if isinstance(data, dict) else None
    if refresh_token:
        return {
            "authenticated": True,
            "status": "healthy",
            "reason": None,
            "message": _get_gmail_status_message(None),
        }
    reason = "missing_refresh_token"
    return {
        "authenticated": False,
        "status": "warning",
        "reason": reason,
        "message": _get_gmail_status_message(reason),
    }


def _get_mcp_servers_status():
    path = pathlib.Path(MCP_JSON_PATH)
    if not path.exists():
        return {"status": "warning", "message": "No MCP config found", "servers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "error", "message": "Invalid MCP config JSON", "servers": []}
    servers = _get_mcp_servers(data)
    if not servers:
        return {"status": "warning", "message": "No MCP servers configured", "servers": []}
    items = []
    for name, server in servers.items():
        command = ""
        enabled = True
        if isinstance(server, dict):
            command = (server.get("command") or "").strip()
            enabled = bool(server.get("enabled", True))
        items.append(
            {
                "name": name,
                "command": command,
                "enabled": enabled,
                "status": "configured" if enabled else "disabled",
            }
        )
    return {"status": "healthy", "message": f"{len(items)} server(s) configured", "servers": items}


def _get_tasks_health_status():
    tasks = _load_tasks()
    total = len(tasks)
    enabled = 0
    running = 0
    errors = 0
    items = []
    for task in tasks.values():
        if task.get("enabled"):
            enabled += 1
        if task.get("last_status") == "running":
            running += 1
        if task.get("last_error") or task.get("last_status") == "error":
            errors += 1
        items.append(
            {
                "id": task.get("id"),
                "name": task.get("name"),
                "enabled": bool(task.get("enabled")),
                "last_error": task.get("last_error"),
            }
        )
    if errors > 0:
        status = "error"
    elif total == 0:
        status = "info"
    elif enabled == 0:
        status = "warning"
    else:
        status = "healthy"
    return {
        "status": status,
        "message": f"{enabled} enabled, {running} running",
        "total": total,
        "enabled": enabled,
        "running": running,
        "errors": errors,
        "tasks": items,
    }


def _get_sessions_health_status():
    sessions = _load_sessions()
    total = len(sessions)
    active_items = []
    for name in sessions:
        status = _get_session_status(name)
        if status and status != "idle":
            active_items.append({"name": name, "status": status})
    active = len(active_items)
    status = "healthy" if active > 0 else "info"
    return {
        "status": status,
        "message": f"{active} active session(s)",
        "total": total,
        "active": active,
        "sessions": active_items,
    }


def _parse_json_events(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "raw", "data": line})
    return events


def _get_latest_assistant_message(session_name):
    if not session_name:
        return ""
    history = _get_history_for_name(session_name)
    messages = history.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return msg.get("text") or ""
    return ""


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
                text_raw = msg.get("text") or ""
                # Handle text being either a string or a list
                if isinstance(text_raw, list):
                    text = "\n".join(str(t) for t in text_raw)
                else:
                    text = str(text_raw)
                return idx, text
    return -1, ""


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
        # Handle text being either a string or a list (defensive programming)
        text_raw = msg.get("text") or ""
        if isinstance(text_raw, list):
            text = "\n".join(str(t) for t in text_raw).strip()
        else:
            text = str(text_raw).strip()
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


def _extract_agent_text_from_events(events):
    if not events:
        return ""
    parts = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        if evt.get("type") != "item.completed":
            continue
        item = evt.get("item") or {}
        if item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


def _session_has_orchestrator(session_name):
    """Check if a session has an enabled orchestrator managing it."""
    if not session_name:
        return False
    try:
        orchestrators = _load_orchestrators()
        for orch_id, orch in orchestrators.items():
            if not orch.get("enabled"):
                continue
            managed = orch.get("managed_sessions") or []
            if session_name in managed:
                return True
    except Exception as e:
        logger.warning(f"Error checking orchestrator for session {session_name}: {e}")
    return False


def _run_orchestrator_decision(orch, session_name, latest_output):
    provider = orch.get("provider") or DEFAULT_PROVIDER
    goal = orch.get("goal") or ""
    managed = orch.get("managed_sessions") or []
    config = _get_provider_config()
    base_prompt = _get_orchestrator_base_prompt(config)

    # Build context for ALL managed sessions (not just the current one)
    session_contexts = []
    for managed_session_name in managed:
        managed_wd = _get_session_workdir(managed_session_name)
        # Use DEFAULT_CWD if no workdir is set for the session
        actual_wd = managed_wd or DEFAULT_CWD
        session_contexts.append(f"  - {managed_session_name}: {actual_wd}")

    session_context_text = "\n".join(session_contexts) if session_contexts else "  (none)"
    history_text = _format_recent_history(session_name, limit=10)
    prompt = f"""You are helping manage an AI coding assistant working on a project.

YOUR JOB:
{base_prompt}

RULES:
- If the goal is achieved, return done
- If you can take another step toward the goal, send a message to continue the work
- Only ask_human if you truly need their input
- Review the conversation history to avoid repeating yourself
- If unsure what to do next, return done

WORKING DIRECTORIES (where the project is stored):
{session_context_text}

You MUST respond using exactly ONE of these JSON formats and nothing else:
{{"action":"continue","message":"your next message to the conversation"}}
{{"action":"done"}}
{{"action":"ask_human","question":"..."}}

GOAL: {goal}

RECENT CONVERSATION:
{history_text or "None"}
"""
    cwd = _safe_cwd(None)
    try:
        if provider == "codex":
            proc, _ = _run_codex_exec(prompt, cwd, json_events=True)
            events = _parse_json_events(proc.stdout or "")
            text = _extract_agent_text_from_events(events) or (proc.stdout or "").strip()
        elif provider == "copilot":
            proc, _ = _run_copilot_exec(prompt, cwd, config=config)
            text = _strip_copilot_footer((proc.stdout or "").strip())
        elif provider == "gemini":
            text = _run_gemini_exec(prompt, [], config=config, cwd=cwd)
        elif provider == "claude":
            text = _run_claude_exec(prompt, config=config, cwd=cwd)
        else:
            return None
    except Exception as exc:
        logger.error(f"[Orchestrator] decision failed: {exc}")
        return None
    parsed = _extract_json_action(text)
    if parsed is None or not isinstance(parsed, dict) or not parsed.get("action"):
        return {"action": "parse_error", "raw": text}
    parsed["_raw"] = text
    return parsed


def _maybe_orchestrator_kickoff(orch_id, orch, session_name):
    if not orch or not session_name:
        return False
    history = _get_history_for_name(session_name)
    if history.get("messages"):
        return False
    for h in (orch.get("history") or []):
        if h.get("action") == "kickoff" and h.get("target_session") == session_name:
            return False
    role = _infer_worker_role(orch.get("goal") or "")
    config = _load_client_config()
    kickoff_template = _get_orchestrator_worker_prompt(config)
    session_workdir = _get_session_workdir(session_name)
    kickoff = _build_worker_kickoff_prompt(
        orch.get("goal") or "",
        role,
        kickoff_template,
        session_workdir,
    )
    _inject_prompt_to_session(session_name, kickoff)
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    _append_orchestrator_history(
        orch_id,
        orch,
        {
            "at": now_iso,
            "action": "kickoff",
            "target_session": session_name,
            "prompt": kickoff,
            "question": "",
            "raw": "",
        },
    )
    return True


def _inject_prompt_to_session(session_name, prompt):
    if not session_name or not prompt:
        return
    if _get_session_status(session_name) == "running":
        return
    provider = _get_session_provider_for_name(session_name)
    resume_session_id = _get_session_id_for_name(session_name)
    if not resume_session_id:
        resume_session_id = _ensure_session_id(session_name, provider)
    session_workdir = _get_session_workdir(session_name)
    cwd = _safe_cwd(session_workdir or None)
    if resume_session_id:
        _append_history(
            resume_session_id,
            session_name,
            {"messages": [{"role": "system", "text": f"[Orchestrator] {prompt}"}], "tool_outputs": []},
        )

    # Broadcast orchestrator message to viewers of this session in real-time
    _broadcast_session_message(session_name, {
        "type": "message",
        "source": "orchestrator",
        "role": "system",
        "text": f"[Orchestrator] {prompt}"
    })

    job_key = f"{provider}:{session_name}"
    start_job = None
    with _JOB_LOCK:
        existing = _JOBS.get(job_key)
        if existing and not existing.done.is_set():
            return
        job = _Job(
            job_key,
            session_name,
            prompt,
            cwd,
            [],
            300,
            resume_session_id,
            False,
            True,
            provider,
        )
        _JOBS[job_key] = job
        start_job = job
    if start_job:
        _set_session_status(session_name, "running")
        # Notify session viewers that status changed to running (show thinking indicator)
        _broadcast_session_message(session_name, {
            "type": "status_change",
            "status": "running"
        })
        _start_job(start_job)


def _schedule_summary(task):
    schedule = task.get("schedule") or {}
    kind = schedule.get("type") or "manual"
    if kind == "interval":
        minutes = schedule.get("minutes")
        return f"Every {minutes} min" if minutes else "Interval"
    if kind == "daily":
        at = schedule.get("time") or ""
        return f"Daily {at}".strip()
    if kind == "weekly":
        days = schedule.get("days") or []
        day_text = ",".join(days)
        at = schedule.get("time") or ""
        if day_text and at:
            return f"Weekly {day_text} {at}"
        if day_text:
            return f"Weekly {day_text}"
        return "Weekly"
    if kind == "monthly":
        day = schedule.get("day_of_month")
        at = schedule.get("time") or ""
        recur = schedule.get("recur_months", 1)
        try:
            recur = int(recur)
        except (TypeError, ValueError):
            recur = 1
        if recur > 1:
            return f"Every {recur} months on day {day} at {at}".strip()
        return f"Monthly on day {day} at {at}".strip()
    if kind == "once":
        at = schedule.get("time") or ""
        return f"Once {at}".strip()
    return "Manual"


def _compute_next_run(task, now=None):
    schedule = task.get("schedule") or {}
    kind = schedule.get("type") or "manual"
    if not now:
        now = datetime.datetime.now()
    if kind == "interval":
        minutes = schedule.get("minutes")
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            return None
        return now + datetime.timedelta(minutes=max(1, minutes))
    if kind in ("daily", "weekly", "once", "monthly"):
        time_str = schedule.get("time") or ""
        try:
            if not time_str:
                raise ValueError("Empty time string")
            parts = time_str.split(":", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid time format: '{time_str}'")
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                raise ValueError(f"Time out of range: {hour}:{minute}")
        except ValueError as e:
            logger.warning(f"Invalid schedule time for task {task.get('id', 'unknown')}: {e}")
            return None
        base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if kind == "daily":
            if base <= now:
                base += datetime.timedelta(days=1)
            return base
        if kind == "once":
            if base <= now:
                return None
            return base
        if kind == "weekly":
            days = schedule.get("days") or []
            if not isinstance(days, list) or not days:
                return None
            day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            target_days = [day_map.get(d.lower()[:3]) for d in days if day_map.get(d.lower()[:3]) is not None]
            if not target_days:
                return None
            current = base
            for offset in range(0, 8):
                candidate = current + datetime.timedelta(days=offset)
                if candidate.weekday() in target_days and candidate > now:
                    return candidate
            return None
        if kind == "monthly":
            # Get day of month (1-31)
            day_of_month = schedule.get("day_of_month")
            try:
                day_of_month = int(day_of_month)
                if day_of_month < 1 or day_of_month > 31:
                    return None
            except (TypeError, ValueError):
                return None
            # Get recurrence (every N months, default 1)
            recur_months = schedule.get("recur_months", 1)
            try:
                recur_months = max(1, int(recur_months))
            except (TypeError, ValueError):
                recur_months = 1
            # Start from start_date if provided, otherwise current month
            start_date_str = schedule.get("start_date")
            if start_date_str:
                try:
                    start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
                    candidate = start_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                except (ValueError, TypeError):
                    candidate = base
            else:
                candidate = base
            # Find next valid occurrence
            # Try current month, then next months
            for _ in range(24):  # Check up to 24 months ahead
                try:
                    # Try to set the day of month
                    if candidate.day != day_of_month:
                        candidate = candidate.replace(day=day_of_month)
                    if candidate > now:
                        return candidate
                except ValueError:
                    # Day doesn't exist in this month (e.g., Feb 31), skip to next month
                    pass
                # Move to next occurrence (add recur_months)
                month = candidate.month + recur_months
                year = candidate.year
                while month > 12:
                    month -= 12
                    year += 1
                try:
                    candidate = candidate.replace(year=year, month=month, day=1)
                except ValueError:
                    return None
            return None
    return None


def _broadcast_tasks_snapshot():
    snapshot = _build_tasks_snapshot()

    # Add to history for reconnection support
    _TASK_STREAM_HISTORY.add(snapshot)

    dead = []
    for q in list(_TASK_SUBSCRIBERS):
        try:
            # Give slow clients 50ms to drain their queue
            q.put(snapshot, timeout=0.05)
        except queue.Full:
            # Client is too slow - disconnect it
            logger.warning("[Backpressure] Disconnecting slow task subscriber (queue full)")
            dead.append(q)
        except Exception as e:
            logger.warning(f"[Backpressure] Error broadcasting to task subscriber: {e}")
            dead.append(q)

    # Remove dead subscribers
    for q in dead:
        _TASK_SUBSCRIBERS.discard(q)


def _build_tasks_snapshot():
    with _TASK_LOCK:
        tasks = _load_tasks()
    def _task_sort_key(task):
        last_run = task.get("last_run")
        try:
            ts = datetime.datetime.fromisoformat(last_run).timestamp() if last_run else 0
        except (TypeError, ValueError):
            ts = 0
        return (-ts, (task.get("name") or "").lower())
    ordered = sorted(tasks.values(), key=_task_sort_key)
    for task in ordered:
        task["schedule_summary"] = _schedule_summary(task)
    return {"count": len(ordered), "tasks": ordered}


_TASK_STREAMS_LOCK = threading.Lock()
_TASK_STREAMS = {}


def _task_stream_subscribe(task_id):
    q = queue.Queue()
    with _TASK_STREAMS_LOCK:
        _TASK_STREAMS.setdefault(task_id, []).append(q)

    def unsubscribe():
        with _TASK_STREAMS_LOCK:
            queues = _TASK_STREAMS.get(task_id) or []
            if q in queues:
                queues.remove(q)
            if not queues and task_id in _TASK_STREAMS:
                _TASK_STREAMS.pop(task_id, None)

    return q, unsubscribe


def _task_stream_publish(task_id, event, data=None):
    payload = {"event": event, "data": data or {}}
    with _TASK_STREAMS_LOCK:
        queues = list(_TASK_STREAMS.get(task_id) or [])

    dead = []
    for q in queues:
        try:
            # Give slow clients 50ms to drain their queue
            q.put(payload, timeout=0.05)
        except queue.Full:
            logger.warning(f"[Backpressure] Disconnecting slow task stream subscriber for {task_id} (queue full)")
            dead.append(q)
        except Exception as e:
            logger.warning(f"[Backpressure] Error publishing to task stream for {task_id}: {e}")
            dead.append(q)

    # Remove dead subscribers
    if dead:
        with _TASK_STREAMS_LOCK:
            task_queues = _TASK_STREAMS.get(task_id)
            if task_queues:
                for q in dead:
                    if q in task_queues:
                        task_queues.remove(q)
                if not task_queues:
                    _TASK_STREAMS.pop(task_id, None)


def _migrate_legacy_files():
    """Migrate old .codex_ prefixed files to new names."""
    migrations = [
        (".codex_sessions.json", SESSION_STORE_PATH),
        (".codex_tasks.json", TASK_STORE_PATH),
        (".codex_history.json", HISTORY_STORE_PATH),
        (".codex_log.jsonl", LOG_STORE_PATH),
        (".client_config.json", CLIENT_CONFIG_PATH),
        (".mcp.json", MCP_JSON_PATH),
    ]
    
    for old_name, new_path in migrations:
        old_path = os.path.join(DEFAULT_CWD, old_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try:
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                shutil.copy2(old_path, new_path)
                logger.info(f"Migrated {old_name} -> {os.path.basename(new_path)}")
            except Exception as e:
                logger.warning(f"Failed to migrate {old_name}: {e}")
    
    # Migrate context directory
    old_context = os.path.join(DEFAULT_CWD, ".codex_sessions")
    if os.path.exists(old_context) and not os.path.exists(CONTEXT_DIR):
        try:
            shutil.copytree(old_context, CONTEXT_DIR)
            logger.info(f"Migrated .codex_sessions/ -> context/")
        except Exception as e:
            logger.warning(f"Failed to migrate context directory: {e}")


def _get_history_path(workdir=None):
    """Get the path to the history file for a given workdir.
    
    Args:
        workdir: Working directory to store history in. If None, uses root location.
    
    Returns:
        Path to the .codex_history.json file
    """
    if workdir:
        # Store history in the workdir
        return os.path.join(workdir, ".codex_history.json")
    else:
        # Fall back to root (backward compatibility)
        return HISTORY_STORE_PATH


def _load_history(workdir=None):
    """Load conversation history from the appropriate directory.
    
    Args:
        workdir: Working directory to load history from. If None, uses root.
    
    Returns:
        dict: History data keyed by session_id
    """
    path = pathlib.Path(_get_history_path(workdir))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _session_has_history(session_name, provider, workdir=None):
    if not session_name or not provider:
        return False
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(session_name) or {}
        session_ids = record.get("session_ids") or {}
        session_id = session_ids.get(provider)
        if not workdir:
            workdir = record.get("workdir")
    if not session_id:
        return False
    history = _load_history(workdir).get(session_id) or {}
    return bool(history.get("messages"))


def _save_history(data, workdir=None):
    """Save conversation history to the appropriate directory.
    
    Args:
        data: History data to save
        workdir: Working directory to save history in. If None, uses root.
    """
    path = pathlib.Path(_get_history_path(workdir))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _generate_session_summary(provider, session_id, session_name, config, workdir=None):
    """Generate markdown summary of session using the current provider.
    
    Args:
        provider: Provider to use for summary generation
        session_id: Session ID to summarize
        session_name: Name of the session
        config: Provider configuration
        workdir: Working directory where history is stored (None = root)
    
    Returns:
        str: Markdown summary of the session
    """
    logger.info(f"[Context] Generating summary for {provider} session {session_id}")
    history = _load_history(workdir).get(session_id)
    if not history or not history.get("messages"):
        logger.warning(f"[Context] No history found for session {session_id}")
        return "No conversation history to summarize."
    
    messages = history.get("messages", [])
    logger.debug(f"[Context] Found {len(messages)} messages in history")
    
    # Format messages in a more readable way
    formatted_messages = []
    for msg in messages[-10:]:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        formatted_messages.append(f"**{role.capitalize()}**: {text}")
    
    messages_text = "\n\n".join(formatted_messages)
    
    summary_prompt = f"""Generate a summary now. Do not ask questions. Just write the summary.

Conversation to summarize:

{messages_text}

Summary structure:
1. **Goals** - user's objectives
2. **Key Points** - main topics
3. **Progress** - accomplishments
4. **Next** - pending items

Write the summary (max 300 words):"""
    
    try:
        logger.debug(f"[Context] Calling {provider} to generate summary (one-off request)")
        if provider == "codex":
            # Use a one-off request (don't resume session - it might respond to old context)
            proc, _ = _run_codex_exec(
                summary_prompt,
                _safe_cwd(None),
                timeout_sec=120,
                json_events=True,
                # Do NOT resume - we want a fresh summary, not a continuation
            )
            result = _build_result(proc, _safe_cwd(None), [], json_events=True, prompt=summary_prompt)
            logger.debug(f"[Context] Result keys: {result.keys()}")
            logger.debug(f"[Context] Conversation: {result.get('conversation')}")
            if result.get("conversation") and result["conversation"].get("messages"):
                logger.debug(f"[Context] Found {len(result['conversation']['messages'])} messages in result")
                for msg in reversed(result["conversation"]["messages"]):
                    if msg.get("role") == "assistant":
                        summary = msg.get("text", "").strip()
                        logger.info(f"[Context] Summary generated: {len(summary)} chars")
                        return summary
            logger.warning(f"[Context] No assistant message found in result")
        elif provider == "copilot":
            proc, _ = _run_copilot_exec(
                summary_prompt,
                _safe_cwd(None),
                config=config,
                timeout_sec=120,
                # Do NOT resume
            )
            return (proc.stdout or "").strip()
        elif provider == "gemini":
            summary = _run_gemini_exec(
                summary_prompt,
                messages[-10:],
                config=config,
                timeout_sec=120,
                cwd=_safe_cwd(None),
                # Do NOT resume
            ).strip()
            logger.info(f"[Context] Summary generated: {len(summary)} chars")
            return summary
        elif provider == "claude":
            summary = _run_claude_exec(
                summary_prompt,
                config=config,
                timeout_sec=120,
                cwd=_safe_cwd(None),
                # Do NOT resume
            )
            summary = (summary or "").strip()
            logger.info(f"[Context] Summary generated: {len(summary)} chars")
            return summary
    except Exception as e:
        logger.error(f"[Context] Error in summary generation: {e}", exc_info=True)
        return f"Error generating summary: {str(e)}"
    
    return "Summary generation failed."


def _load_session_context(session_name):
    """Load context briefing from session context file."""
    if not session_name:
        return None
    
    try:
        context_dir = pathlib.Path(CONTEXT_DIR)
        context_file = context_dir / f"{session_name}_context.md"
        
        if not context_file.exists():
            logger.debug(f"[Context] No context file found for session {session_name}")
            return None
        
        content = context_file.read_text(encoding="utf-8")
        logger.info(f"[Context] Loaded context for {session_name}: {len(content)} chars")
        return content
    except Exception as e:
        logger.error(f"[Context] Error loading context: {e}", exc_info=True)
        return None


def _append_context_briefing(session_name, summary_markdown, from_provider, to_provider):
    """Append timestamped summary to session context file."""
    try:
        logger.info(f"[Context] Appending briefing to {session_name}_context.md")
        context_dir = pathlib.Path(CONTEXT_DIR)
        context_dir.mkdir(parents=True, exist_ok=True)
        
        context_file = context_dir / f"{session_name}_context.md"
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        briefing = f"\n## {timestamp} - Switching from {from_provider} to {to_provider}\n\n{summary_markdown}\n"
        
        with context_file.open("a", encoding="utf-8") as f:
            f.write(briefing)
        
        logger.info(f"[Context] Briefing saved to {context_file}")
        return True
    except Exception as e:
        logger.error(f"[Context] Error appending context briefing: {e}", exc_info=True)
        return False


def _load_context_briefing_text(session_name):
    if not session_name:
        return ""
    context_file = pathlib.Path(CONTEXT_DIR) / f"{session_name}_context.md"
    if not context_file.exists():
        return ""
    try:
        return context_file.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError as e:
        logger.warning(f"Cannot read context file for {session_name}: {e}")
        return ""


def _slice_context_tail(text, max_chars=4000):
    text = (text or "").strip()
    if not text or len(text) <= max_chars:
        return text
    try:
        import re
        headings = [m.start() for m in re.finditer(r"^## ", text, flags=re.MULTILINE)]
        for start in reversed(headings):
            if len(text) - start <= max_chars:
                return text[start:].lstrip()
    except (ImportError, re.error, AttributeError) as e:
        logger.debug(f"Cannot slice context at heading boundaries: {e}")
    return text[-max_chars:].lstrip()


def _build_cross_provider_prompt(prompt, context_text):
    if not prompt or not context_text:
        return prompt
    marker = "Cross-provider context summary"
    if marker in prompt:
        return prompt
    return f"{marker}:\n{context_text}\n\nUser request:\n{prompt}"


def _log_event(payload):
    try:
        path = pathlib.Path(LOG_STORE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, json.JSONEncodeError) as e:
        # Log to stderr since we can't write to log file
        logger.error(f"Failed to write event log: {e}")


def _append_history(session_id, session_name, conversation):
    """Append conversation to history in the appropriate directory.

    Args:
        session_id: Session identifier
        session_name: Name of the session (to look up workdir)
        conversation: Conversation data with messages and tool_outputs
    """
    if not session_id or not conversation:
        logger.debug(f"[History] Skipped: session_id={session_id}, conversation={bool(conversation)}")
        return
    messages = conversation.get("messages") or []
    tool_outputs = conversation.get("tool_outputs") or []
    if not messages and not tool_outputs:
        logger.debug(f"[History] Skipped: no messages or tool_outputs for session_id={session_id}")
        return

    # Get workdir from session record
    workdir = None
    if session_name:
        with _SESSION_LOCK:
            sessions = _load_sessions()
            record = sessions.get(session_name) or {}
            workdir = record.get("workdir")

    logger.info(f"[History] Appending session_id={session_id}, session_name={session_name}, workdir={workdir}, messages={len(messages)}, tool_outputs={len(tool_outputs)}")

    with _SESSION_LOCK:
        data = _load_history(workdir)
        logger.debug(f"[History] Loaded history with {len(data)} sessions")
        entry = data.get(session_id) or {"session_id": session_id, "messages": [], "tool_outputs": []}
        if session_name:
            entry["session_name"] = session_name
        entry["messages"].extend(messages)
        entry["tool_outputs"].extend(tool_outputs)
        data[session_id] = entry
        logger.info(f"[History] Saving session_id={session_id} with {len(entry['messages'])} total messages to workdir={workdir}")
        _save_history(data, workdir)


def _migrate_history_session_id(session_name, old_id, new_id):
    if not session_name or not old_id or not new_id or old_id == new_id:
        return
    # Get workdir from session record
    workdir = None
    with _SESSION_LOCK:
        sessions = _load_sessions()
        record = sessions.get(session_name) or {}
        workdir = record.get("workdir")
    data = _load_history(workdir)
    old_entry = data.get(old_id)
    if not old_entry:
        return
    new_entry = data.get(new_id) or {"session_id": new_id, "messages": [], "tool_outputs": []}
    new_entry["messages"].extend(old_entry.get("messages") or [])
    new_entry["tool_outputs"].extend(old_entry.get("tool_outputs") or [])
    new_entry["session_name"] = session_name
    data[new_id] = new_entry
    data.pop(old_id, None)
    _save_history(data, workdir)




def _build_master_snapshot():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    session_list = _build_session_list(sessions)
    items = []
    for item in session_list:
        name = item.get("name")
        text = _get_latest_assistant_message(name)
        if text:
            items.append(
                {
                    "session_name": name,
                    "text": text,
                    "last_used": item.get("last_used") or "",
                    "created_at": item.get("created_at") or "",
                }
            )
    # Oldest to newest so the latest appears at the bottom.
    items.sort(key=lambda x: (x.get("last_used") or x.get("created_at") or ""))
    return {"messages": items}


def _broadcast_master_message(session_name, text_or_payload):
    """Broadcast message to master console subscribers.

    Args:
        session_name: Session name for context
        text_or_payload: Either a string (simple message) or dict (structured payload)
    """
    if not session_name:
        return

    # Support both simple text and structured payloads
    if isinstance(text_or_payload, dict):
        payload = text_or_payload
    else:
        if not text_or_payload:
            return
        payload = {"type": "message", "session_name": session_name, "text": text_or_payload}

    # Add to history for reconnection support
    _MASTER_STREAM_HISTORY.add(payload)

    dead = []
    for q in list(_MASTER_SUBSCRIBERS):
        try:
            # Give slow clients 50ms to drain their queue
            q.put(payload, timeout=0.05)
        except queue.Full:
            logger.warning(f"[Backpressure] Disconnecting slow master subscriber (queue full)")
            dead.append(q)
        except Exception as e:
            logger.warning(f"[Backpressure] Failed to broadcast to master subscriber: {e}")
            dead.append(q)
    for q in dead:
        _MASTER_SUBSCRIBERS.discard(q)


def _broadcast_session_message(session_name, payload):
    """Broadcast a message to all viewers of a specific session.

    Args:
        session_name: The session to broadcast to
        payload: Dict with message data (type, source, role, text, etc.)
    """
    if not session_name or not payload:
        return

    # Add to per-session history for reconnection support
    if session_name not in _SESSION_MESSAGE_HISTORY:
        _SESSION_MESSAGE_HISTORY[session_name] = _StreamHistory(maxlen=500)
    _SESSION_MESSAGE_HISTORY[session_name].add(payload)

    viewers = _SESSION_VIEWERS.get(session_name, set())
    dead = []
    for q in list(viewers):
        try:
            # Give slow clients 50ms to drain their queue
            q.put(payload, timeout=0.05)
        except queue.Full:
            logger.warning(f"[Backpressure] Disconnecting slow session viewer for {session_name} (queue full)")
            dead.append(q)
        except Exception as e:
            logger.warning(f"[Backpressure] Failed to broadcast to session {session_name} viewer: {e}")
            dead.append(q)
    for q in dead:
        viewers.discard(q)
        if not viewers:
            _SESSION_VIEWERS.pop(session_name, None)


def _resolve_provider(session_name, requested_provider):
    provider = (requested_provider or "").strip().lower()
    if provider and provider not in SUPPORTED_PROVIDERS:
        raise ValueError("unknown provider")
    if not session_name:
        return provider or DEFAULT_PROVIDER
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(session_name)
        if not record:
            record = {"session_id": None, "provider": provider or DEFAULT_PROVIDER}
            data[session_name] = record
            _save_sessions(data)
        current = (record.get("provider") or DEFAULT_PROVIDER).lower()
        if provider and provider != current:
            if _get_session_status(session_name) == "running":
                raise RuntimeError("session is running; cannot switch provider")
            record["provider"] = provider
            record["session_id"] = None
            data[session_name] = record
            _save_sessions(data)
            return provider
        return current


def _get_claude_history(session_id, workdir):
    """Read conversation history from Claude Code JSONL session file.

    Args:
        session_id: Claude session UUID
        workdir: Working directory where Claude stores session files

    Returns:
        dict: History with messages and tool_outputs
    """
    import os
    import json
    import glob

    # Claude stores sessions in ~/.claude/projects/<workdir-safe-name>/<session-id>.jsonl
    if not workdir:
        workdir = os.getcwd()

    # Convert workdir to Claude's safe project name format
    safe_workdir = workdir.replace(":", "-").replace("\\", "-").replace("/", "-")
    if safe_workdir.startswith("-"):
        safe_workdir = safe_workdir[1:]

    claude_projects_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects")

    # Use glob to find the file - bypasses Windows file system caching issues
    # where os.path.exists() and open() fail even when file exists
    pattern = os.path.join(claude_projects_dir, "*", f"{session_id}.jsonl")
    matches = glob.glob(pattern)

    if not matches:
        logger.debug(f"[Claude History] No session file found for {session_id}")
        return {"messages": [], "tool_outputs": []}

    session_file = matches[0]
    messages = []
    tool_outputs = []

    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    entry_type = entry.get("type")

                    # User messages
                    if entry_type == "user":
                        msg = entry.get("message", {})
                        content = msg.get("content", "")

                        # Handle both string content and array of content blocks
                        if isinstance(content, list):
                            texts = []
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    texts.append(block.get("text", ""))
                            if texts:
                                messages.append({"role": "user", "text": "\n".join(texts)})
                        elif content:
                            messages.append({"role": "user", "text": content})

                    # Assistant messages
                    elif entry_type == "assistant":
                        msg = entry.get("message", {})
                        content_blocks = msg.get("content", [])
                        texts = []
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                        if texts:
                            messages.append({"role": "assistant", "text": "\n".join(texts)})

                    # Tool outputs (bash commands, etc.)
                    elif entry_type == "tool_result":
                        output = entry.get("content", "")
                        if output:
                            tool_outputs.append(output)

                except json.JSONDecodeError as e:
                    logger.warning(f"[Claude History] Failed to parse line: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"[Claude History] Error processing entry: {e}")
                    continue

    except FileNotFoundError:
        logger.debug(f"[Claude History] Session file not found: {session_file}")
        return {"messages": [], "tool_outputs": []}
    except OSError as e:
        logger.error(f"[Claude History] Failed to read session file {session_file}: {e}")
        return {"messages": [], "tool_outputs": []}
    except Exception as e:
        logger.error(f"[Claude History] Unexpected error reading Claude history: {e}", exc_info=True)
        return {"messages": [], "tool_outputs": []}

    logger.debug(f"[Claude History] Loaded {len(messages)} messages, {len(tool_outputs)} tool outputs from {session_file}")
    return {
        "messages": messages,
        "tool_outputs": tool_outputs,
    }


def _get_history_for_name(name):
    """Get conversation history for a named session.

    Args:
        name: Session name

    Returns:
        dict: History with messages and tool_outputs
    """
    if not name:
        return {"messages": [], "tool_outputs": []}
    with _SESSION_LOCK:
        sessions = _load_sessions()
        record = sessions.get(name) or {}
        provider = (record.get("provider") or DEFAULT_PROVIDER).lower()
        session_ids = record.get("session_ids") or {}
        session_id = session_ids.get(provider) or record.get("session_id")
        workdir = record.get("workdir")  # Get workdir from session
        if not session_id:
            return {"messages": [], "tool_outputs": []}

        # Claude sessions are stored in JSONL files, not history.json
        if provider == "claude":
            return _get_claude_history(session_id, workdir)

        # Other providers (codex, copilot, gemini) use history.json
        history = _load_history(workdir).get(session_id) or {}
        return {
            "messages": history.get("messages") or [],
            "tool_outputs": history.get("tool_outputs") or [],
        }


def _events_to_conversation(events, prompt=None):
    messages = []
    tool_outputs = []
    if prompt:
        messages.append({"role": "user", "text": prompt})
    assistant_chunks = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        if evt.get("type") != "item.completed":
            continue
        item = evt.get("item") or {}
        item_type = item.get("type")
        if item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                assistant_chunks.append(text)
        elif item_type == "command_execution":
            output = item.get("aggregated_output") or ""
            if output:
                tool_outputs.append(output)
    if assistant_chunks:
        messages.append({"role": "assistant", "text": "\n".join(assistant_chunks).strip()})
    return {"messages": messages, "tool_outputs": tool_outputs}


def _build_result(proc, cwd, cmd, json_events, prompt=None):
    result = {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "cwd": cwd,
        "cmd": cmd,
    }
    if json_events:
        events = _parse_json_events(proc.stdout)
        result["events"] = events
        session_id = _extract_session_id(events)
        result["session_id"] = session_id
        result["thread_id"] = session_id
        result["conversation"] = _events_to_conversation(events, prompt=prompt)
    return result


def _build_synthetic_events(text):
    if not text:
        return []
    return [{"type": "item.completed", "item": {"type": "agent_message", "text": text}}]


@APP.get("/health/dashboard")
def health_dashboard():
    """Render the health dashboard UI."""
    return render_template("health_dashboard.html")


@APP.get("/api/health/full")
def health_full():
    """Get comprehensive health status for all bil-dir components."""
    config = _load_client_config()
    provider_status = _provider_path_status(config)
    gmail_status = _gmail_auth_status()

    providers = {}
    for provider_name in sorted(SUPPORTED_PROVIDERS):
        is_available = provider_status.get(provider_name, False)
        providers[provider_name] = {
            "status": "healthy" if is_available else "error",
            "message": "CLI found" if is_available else "CLI not found in PATH",
            "available": is_available,
        }

    mcp_servers = _get_mcp_servers_status()
    tasks = _get_tasks_health_status()
    sessions = _get_sessions_health_status()
    uptime_seconds = int(time.time() - APP_START_TIME)

    component_statuses = [gmail_status.get("status"), mcp_servers.get("status"), tasks.get("status"), sessions.get("status")]
    component_statuses.extend([value.get("status") for value in providers.values()])

    if "error" in component_statuses:
        overall_status = "error"
    elif "warning" in component_statuses:
        overall_status = "warning"
    elif "info" in component_statuses:
        overall_status = "info"
    else:
        overall_status = "healthy"

    return jsonify(
        {
            "overall_status": overall_status,
            "timestamp": datetime.datetime.now().isoformat(),
            "server_time": datetime.datetime.now().isoformat(timespec="seconds"),
            "uptime_seconds": uptime_seconds,
            "uptime_human": _format_duration(uptime_seconds),
            "providers": providers,
            "gmail_mcp": gmail_status,
            "mcp_servers": mcp_servers,
            "tasks": tasks,
            "sessions": sessions,
        }
    )


@APP.get("/api/health/logs")
def health_logs():
    limit_raw = request.args.get("limit", "50")
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))
    path = pathlib.Path(LOG_STORE_PATH)
    entries = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as e:
            logger.warning(f"Cannot read log file: {e}")
            lines = []
        for line in lines[-limit:]:
            timestamp = ""
            try:
                payload = json.loads(line)
                ts = payload.get("ts") or payload.get("timestamp")
                if ts is not None:
                    timestamp = datetime.datetime.fromtimestamp(float(ts)).isoformat()
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
                logger.debug(f"Cannot parse log line timestamp: {e}")
                timestamp = ""
            entries.append({"timestamp": timestamp, "file": path.name, "message": line})
    return jsonify({"logs": entries})


@APP.get("/api/health/test-provider/<provider>")
def health_test_provider(provider):
    provider = (provider or "").lower()
    if provider not in SUPPORTED_PROVIDERS:
        return jsonify({"status": "error", "message": "Unknown provider", "available": False}), 400
    config = _load_client_config()
    status = _provider_path_status(config)
    is_available = bool(status.get(provider))
    return jsonify(
        {
            "status": "healthy" if is_available else "error",
            "message": "CLI found" if is_available else "CLI not found in PATH",
            "available": is_available,
        }
    )


@APP.post("/gmail/reauth")
def gmail_reauth():
    npx_path = _resolve_npx_path()
    if not npx_path:
        return _error_response("npx not found in PATH", code="NPX_NOT_FOUND", status=500)
    cmd = [npx_path, "-y", "@gongrzhe/server-gmail-autoauth-mcp", "auth"]
    try:
        creds_path = pathlib.Path.home() / ".gmail-mcp" / "credentials.json"
        try:
            if creds_path.exists():
                creds_path.unlink()
        except OSError as e:
            logger.warning(f"Cannot delete old Gmail credentials: {e}")
        if os.name == "nt":
            console_cmd = ["cmd", "/c", "start"] + cmd
            subprocess.Popen(console_cmd, cwd=DEFAULT_CWD)
        elif sys.platform == "darwin":
            # Open a new Terminal window on macOS to show the auth flow
            full_cmd = " ".join(cmd)
            osa_cmd = [
                "osascript",
                "-e",
                f'tell application "Terminal" to do script "{full_cmd}"'
            ]
            subprocess.Popen(osa_cmd, cwd=DEFAULT_CWD)
        else:
            subprocess.Popen(
                cmd,
                cwd=DEFAULT_CWD,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
    except Exception as exc:
        logger.error(f"Gmail reauth failed: {exc}", exc_info=True)
        return _error_response(str(exc), code="REAUTH_FAILED", status=500)
    return jsonify({"ok": True})


@APP.get("/health")
def health():
    return jsonify({"ok": True})


@APP.get("/diag")
def diag():
    ip_hint = ""
    try:
        ip_hint = subprocess.check_output("ipconfig", text=True, encoding="utf-8", errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        logger.debug(f"Cannot get ipconfig output: {e}")
        ip_hint = ""
    template_path = APP.jinja_loader.searchpath if APP.jinja_loader else []
    template_has_task_menu = False
    try:
        tmpl_path = os.path.join(APP.root_path, "templates", "chat.html")
        if os.path.exists(tmpl_path):
            template_has_task_menu = "task-menu" in pathlib.Path(tmpl_path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        logger.debug(f"Cannot read template file for diagnostics: {e}")
        template_has_task_menu = False
    return jsonify(
        {
            "CODEX_CWD": os.environ.get("CODEX_CWD"),
            "CODEX_PATH": os.environ.get("CODEX_PATH"),
            "CODEX_SKIP_GIT_CHECK": os.environ.get("CODEX_SKIP_GIT_CHECK"),
            "resolved_codex": shutil.which("codex") or shutil.which("codex.cmd"),
            "server_cwd": os.getcwd(),
            "template_searchpath": template_path,
            "template_has_task_menu": template_has_task_menu,
            "ipconfig": ip_hint,
        }
    )


@APP.get("/diag/ui")
def diag_ui():
    ipconfig = ""
    try:
        ipconfig = subprocess.check_output("ipconfig", text=True, encoding="utf-8", errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        logger.debug(f"Cannot get ipconfig output for diag UI: {e}")
        ipconfig = "Unable to read ipconfig output."
    port = int(os.environ.get("PORT", "6000"))
    return render_template("diag.html", ipconfig=ipconfig, port=port)


@APP.get("/diag/home")
def diag_home():
    home = str(pathlib.Path.home())
    session_state = pathlib.Path.home() / ".copilot" / "session-state"
    exists = session_state.exists()
    sample = []
    if exists:
        try:
            sample = sorted([p.name for p in session_state.iterdir() if p.is_dir()])[-5:]
        except (OSError, PermissionError) as e:
            logger.debug(f"Cannot read Copilot session-state directory: {e}")
            sample = []
    return jsonify({"home": home, "session_state_exists": exists, "sample_dirs": sample})


@APP.post("/pick-workdir")
def pick_workdir():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return jsonify({"error": f"tkinter not available: {exc}"}), 500
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory()
        root.destroy()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if not path:
        return jsonify({"cancelled": True}), 200
    return jsonify({"path": path})


@APP.get("/config")
def config_ui():
    config = _load_client_config()
    return render_template(
        "config.html",
        config=config,
        providers=sorted(SUPPORTED_PROVIDERS),
        provider_status=_provider_path_status(config),
        provider_models=_get_provider_model_info(),
        gmail_status=_gmail_auth_status(),
        orch_base_prompt=(config.get("orch_base_prompt") or "").strip() or DEFAULT_ORCH_BASE_PROMPT,
        orch_default_prompt=DEFAULT_ORCH_BASE_PROMPT,
        orch_worker_prompt=(config.get("orch_worker_prompt") or "").strip() or DEFAULT_ORCH_WORKER_PROMPT,
        orch_worker_default_prompt=DEFAULT_ORCH_WORKER_PROMPT,
    )


@APP.post("/config")
def config_save():
    form = request.form
    data = _load_client_config()
    data["full_permissions"] = form.get("full_permissions") == "on"
    data["default_workdir"] = (form.get("default_workdir") or "").strip()
    data["full_permissions_codex"] = form.get("full_permissions_codex") == "on"
    data["copilot_permissions"] = (form.get("copilot_permissions") or "").strip()
    data["full_permissions_gemini"] = form.get("full_permissions_gemini") == "on"
    data["full_permissions_claude"] = form.get("full_permissions_claude") == "on"
    data["sandbox_mode_codex"] = (form.get("sandbox_mode_codex") or "").strip()
    data["sandbox_mode_gemini"] = (form.get("sandbox_mode_gemini") or "").strip()
    data["sandbox_mode_claude"] = (form.get("sandbox_mode_claude") or "").strip()
    data["gemini_path"] = (form.get("gemini_path") or "").strip()
    data["claude_path"] = (form.get("claude_path") or "").strip()
    data["copilot_path"] = (form.get("copilot_path") or "").strip()
    data["copilot_model"] = (form.get("copilot_model") or "").strip()
    data["mcp_json"] = (form.get("mcp_json") or "").strip()
    data["orch_base_prompt"] = (form.get("orch_base_prompt") or "").strip()
    data["orch_worker_prompt"] = (form.get("orch_worker_prompt") or "").strip()
    if "copilot_token" in form:
        data["copilot_token"] = (form.get("copilot_token") or "").strip()
    if "copilot_token_env" in form:
        data["copilot_token_env"] = (form.get("copilot_token_env") or "GH_TOKEN").strip() or "GH_TOKEN"
    error = None
    try:
        mcp_data = _load_mcp_json(data)
        if mcp_data:
            _write_mcp_json_file(mcp_data)
            _write_codex_mcp_config(mcp_data)
    except ValueError as exc:
        error = str(exc)
    _save_client_config(data)
    return render_template(
        "config.html",
        config=data,
        providers=sorted(SUPPORTED_PROVIDERS),
        provider_status=_provider_path_status(data),
        provider_models=_get_provider_model_info(),
        saved=error is None,
        error=error,
        gmail_status=_gmail_auth_status(),
        orch_base_prompt=(data.get("orch_base_prompt") or "").strip() or DEFAULT_ORCH_BASE_PROMPT,
        orch_default_prompt=DEFAULT_ORCH_BASE_PROMPT,
        orch_worker_prompt=(data.get("orch_worker_prompt") or "").strip() or DEFAULT_ORCH_WORKER_PROMPT,
        orch_worker_default_prompt=DEFAULT_ORCH_WORKER_PROMPT,
    )


@APP.get("/diag/providers")
def diag_providers():
    config = _load_client_config()
    return jsonify(_provider_path_status(config))

@APP.get("/diag/models")
def diag_models():
    return jsonify(_get_provider_model_info())


@APP.get("/")
def home():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    gmail_status = _gmail_auth_status()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        selected_provider=DEFAULT_PROVIDER,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        gmail_status=gmail_status,
    )


@APP.get("/usage")
def usage_page():
    """Display API usage statistics page"""
    return render_template("usage.html")


@APP.get("/chat")
def chat_home():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    gmail_status = _gmail_auth_status()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        selected_provider=DEFAULT_PROVIDER,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        gmail_status=gmail_status,
    )


@APP.get("/chat/<name>")
def chat_named(name):
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    history = _get_history_for_name(name)
    history_messages = history.get("messages") or []
    if not any((m.get("role") == "system" and str(m.get("text", "")).startswith("[Orchestrator]")) for m in history_messages):
        kickoff_prompt = None
        with _ORCH_LOCK:
            orchestrators = _load_orchestrators()
        for orch in orchestrators.values():
            managed = orch.get("managed_sessions") or []
            if name not in managed:
                continue
            for entry in reversed(orch.get("history") or []):
                if entry.get("action") == "kickoff" and entry.get("prompt"):
                    kickoff_prompt = entry.get("prompt")
                    break
            if kickoff_prompt:
                break
        if kickoff_prompt:
            history_messages = [{"role": "system", "text": f"[Orchestrator] {kickoff_prompt}"}] + history_messages
    history = {"messages": history_messages, "tool_outputs": history.get("tool_outputs") or []}
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    selected_provider = _get_session_provider_for_name(name)
    _touch_session(name)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    gmail_status = _gmail_auth_status()
    # Get session-specific workdir if set
    session_record = sessions.get(name) or {}
    session_workdir = (session_record.get("workdir") or "").strip() if isinstance(session_record, dict) else ""
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        selected=name,
        selected_provider=selected_provider,
        default_provider=DEFAULT_PROVIDER,
        history_messages=history["messages"],
        history_tools=history["tool_outputs"],
        default_workdir=default_workdir,
        session_workdir=session_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        gmail_status=gmail_status,
    )


@APP.get("/master")
def master_view():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    master_snapshot = _build_master_snapshot()
    gmail_status = _gmail_auth_status()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        selected_provider=DEFAULT_PROVIDER,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        view_mode="master",
        master_messages=master_snapshot.get("messages") or [],
        gmail_status=gmail_status,
    )


@APP.get("/task/<task_id>")
def task_view(task_id):
    with _SESSION_LOCK:
        sessions = _load_sessions()
    with _TASK_LOCK:
        tasks = _load_tasks()
    
    task = tasks.get(task_id)
    if not task:
        return "Task not found", 404

    updated_task = False
    if task.get("last_output") and not task.get("last_output_raw"):
        raw = task.get("last_output")
        cleaned = _extract_codex_assistant_output(raw)
        task["last_output_raw"] = raw
        if cleaned != raw:
            task["last_output"] = cleaned
        updated_task = True
    task = _ensure_task_history(task)
    if task.get("run_history") and tasks.get(task_id, {}).get("run_history") != task.get("run_history"):
        updated_task = True
    if updated_task:
        with _TASK_LOCK:
            tasks = _load_tasks()
            tasks[task["id"]] = task
            _save_tasks(tasks)

    output_history_text = _build_task_history_text(task.get("run_history"), "output")
    raw_history_text = _build_task_history_text(task.get("run_history"), "raw_output")
    task["output_history_text"] = output_history_text or (task.get("last_output") or "")
    task["raw_output_history_text"] = raw_history_text or (task.get("last_output_raw") or "")
    
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    gmail_status = _gmail_auth_status()
    prompt_text = (task.get("prompt") or task.get("command") or "")
    task_mentions_gmail = "gmail" in prompt_text.lower()
    
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        selected_task=task,
        view_mode="task",
        is_new_task=False,
        gmail_status=gmail_status,
        task_mentions_gmail=task_mentions_gmail,
    )


@APP.get("/tasks/<task_id>")
def get_task(task_id):
    with _TASK_LOCK:
        tasks = _load_tasks()
    task = tasks.get(task_id)
    if not task:
        return _error_response("Task not found", code=ERR_TASK_NOT_FOUND, status=404)

    updated_task = False
    if task.get("last_output") and not task.get("last_output_raw"):
        raw = task.get("last_output")
        cleaned = _extract_codex_assistant_output(raw)
        task["last_output_raw"] = raw
        if cleaned != raw:
            task["last_output"] = cleaned
        updated_task = True
    task = _ensure_task_history(task)
    if task.get("run_history") and tasks.get(task_id, {}).get("run_history") != task.get("run_history"):
        updated_task = True
    if updated_task:
        with _TASK_LOCK:
            tasks = _load_tasks()
            tasks[task["id"]] = task
            _save_tasks(tasks)

    output_history_text = _build_task_history_text(task.get("run_history"), "output")
    raw_history_text = _build_task_history_text(task.get("run_history"), "raw_output")
    task["output_history_text"] = output_history_text or (task.get("last_output") or "")
    task["raw_output_history_text"] = raw_history_text or (task.get("last_output_raw") or "")
    return jsonify({"task": task})


@APP.get("/task/new")
def task_new():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orchestrators = _build_orchestrator_list()
    gmail_status = _gmail_auth_status()
    empty_task = {
        "id": "",
        "name": "",
        "prompt": "",
        "provider": DEFAULT_PROVIDER,
        "schedule": {"type": "manual"},
        "enabled": True,
        "workdir": "",
        "last_output": "",
        "last_output_raw": "",
        "last_error": "",
        "last_runtime_sec": None,
        "run_history": [],
        "output_history_text": "",
        "raw_output_history_text": "",
    }
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orchestrators,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        selected_task=empty_task,
        view_mode="task",
        is_new_task=True,
        gmail_status=gmail_status,
        task_mentions_gmail=False,
    )


@APP.get("/orchestrator/<orch_id>")
def orchestrator_view(orch_id):
    with _SESSION_LOCK:
        sessions = _load_sessions()
    with _ORCH_LOCK:
        orchestrators = _load_orchestrators()
    orch = orchestrators.get(orch_id)
    if not orch:
        return "Orchestrator not found", 404
    config = _load_client_config()
    available_providers = _get_available_providers(config)
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    orch_list = _build_orchestrator_list()
    history_text = _build_orchestrator_history_text(orch.get("history"))
    orch["history_text"] = history_text
    gmail_status = _gmail_auth_status()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        orchestrators=orch_list,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        available_providers=available_providers,
        selected_orchestrator=orch,
        view_mode="orchestrator",
        gmail_status=gmail_status,
    )


@APP.post("/launch")
def launch():
    session_name = (request.form.get("session_name") or "").strip()
    prompt = (request.form.get("prompt") or "").strip()
    force_new = request.form.get("force_new") == "on"
    resume_last = request.form.get("resume_last") == "on"
    if not session_name:
        return render_template("result.html", error="session_name is required"), 400
    if not prompt:
        return render_template("result.html", error="prompt is required"), 400
    resume_session_id = None if force_new else _get_session_id_for_name(session_name)
    try:
        provider = _resolve_provider(session_name, "codex")
        if provider != "codex":
            return render_template("result.html", error="launcher only supports codex"), 400
        _set_session_status(session_name, "running")
        proc, cmd = _run_codex_exec(
            prompt,
            _safe_cwd(None),
            extra_args=None,
            timeout_sec=300,
            resume_session_id=resume_session_id,
            resume_last=resume_last and not resume_session_id,
            json_events=True,
        )
        result = _build_result(proc, _safe_cwd(None), cmd, json_events=True, prompt=prompt)
        if result.get("session_id"):
            _set_session_name(session_name, result["session_id"], "codex")
            if result.get("conversation"):
                _append_history(result["session_id"], session_name, result["conversation"])
        return render_template(
            "result.html",
            result=result,
            session_name=session_name,
            cmd=cmd,
        )
    except Exception as exc:
        return render_template("result.html", error=html.escape(str(exc))), 500
    finally:
        _set_session_status(session_name, "idle")


@APP.post("/exec")
def exec_codex():
    # Direct file write for debugging
    with open("exec_debug.txt", "a") as f:
        f.write(f"[{datetime.datetime.now()}] /exec called\n")
        f.flush()
    
    logger.debug("[Context] /exec endpoint called")
    body, err = _require_json_body()
    if err:
        return err
    prompt = body.get("prompt")
    extra_args = body.get("extra_args") or []
    timeout_sec = body.get("timeout_sec", 300)
    resume_session_id = body.get("session_id")
    session_name = body.get("session_name")
    requested_provider = body.get("provider")
    logger.debug(f"[Context] /exec: session={session_name}, provider={requested_provider}")
    resume_last = bool(body.get("resume_last", False))
    json_events = bool(body.get("json_events", True))
    if session_name:
        name_err = _validate_name(session_name, "session_name")
        if name_err:
            return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
    if requested_provider:
        provider_err = _validate_provider(requested_provider)
        if provider_err:
            return _error_response(provider_err, code=ERR_INVALID_PROVIDER, status=400)
    if not isinstance(prompt, str) or not prompt.strip():
        return _error_response("prompt must be a non-empty string", code=ERR_INVALID_PROMPT, status=400)
    if not isinstance(extra_args, list) or not all(isinstance(x, str) for x in extra_args):
        return _error_response("extra_args must be a list of strings", code=ERR_INVALID_INPUT, status=400)
    if not isinstance(timeout_sec, int) or timeout_sec <= 0 or timeout_sec > 3600:
        return _error_response("timeout_sec must be an integer between 1 and 3600", code=ERR_INVALID_TIMEOUT, status=400)
    try:
        cwd = _safe_cwd(body.get("cwd"))
        # Capture current state BEFORE resolving provider (for context detection)
        current_provider_before = None
        current_session_id_before = None
        if session_name:
            current_provider_before = _get_session_provider_for_name(session_name)
            with _SESSION_LOCK:
                data = _load_sessions()
                record = data.get(session_name) or {}
                session_ids = record.get("session_ids") or {}
                # Get session ID for CURRENT provider (before switch)
                current_session_id_before = session_ids.get(current_provider_before)
            logger.debug(f"[Context] Before resolve: provider={current_provider_before}, session_id={current_session_id_before}, session_ids={session_ids}")
        
        provider = _resolve_provider(session_name, requested_provider)
        
        # Only resume if THIS provider already has a session ID
        if not resume_session_id and session_name:
            with _SESSION_LOCK:
                data = _load_sessions()
                record = data.get(session_name) or {}
                session_ids = record.get("session_ids") or {}
                resume_session_id = session_ids.get(provider)  # Get session for THIS provider only
        logger.debug(f"[Context] After resolve: provider={provider}, requested={requested_provider}")
        
        # Check if we're switching providers and need to generate context
        switching_providers = False
        context_summary = ""
        if session_name and current_provider_before and provider != current_provider_before:
            logger.info(f"[Context] Provider changed: {current_provider_before} -> {provider}")
            # Get session_ids for the NEW provider
            with _SESSION_LOCK:
                data = _load_sessions()
                record = data.get(session_name) or {}
                session_ids = record.get("session_ids") or {}
                new_provider_session_id = session_ids.get(provider)
            logger.debug(f"[Context] New provider session_id: {new_provider_session_id}")
            
            # If new provider doesn't have a session yet, we're starting fresh - generate summary
            if not new_provider_session_id and current_session_id_before:
                switching_providers = True
                logger.info(f"[Context] Switching from {current_provider_before} to {provider} in session {session_name}")
                try:
                    config = _get_provider_config()
                    workdir = record.get("workdir")  # Get workdir from session record
                    summary = _generate_session_summary(
                        current_provider_before,
                        current_session_id_before,
                        session_name,
                        config,
                        workdir
                    )
                    _append_context_briefing(session_name, summary, current_provider_before, provider)
                    logger.info(f"[Context] Summary generated and saved")
                except Exception as e:
                    logger.error(f"[Context] Error generating summary: {e}", exc_info=True)
            else:
                logger.debug(f"[Context] Not generating: new_session_id={new_provider_session_id}, old_session_id={current_session_id_before}")
        
        # Load context briefing for new provider sessions (when provider just switched)
        context_briefing = None
        if session_name:
            # Check if this is a new session for this provider
            with _SESSION_LOCK:
                data = _load_sessions()
                record = data.get(session_name) or {}
                session_ids = record.get("session_ids") or {}
                provider_has_session = session_ids.get(provider)
            
            if not provider_has_session:
                context_briefing = _load_session_context(session_name)
        
        if session_name:
            _set_session_status(session_name, "running")
        if provider == "codex":
            proc, cmd = _run_codex_exec(
                prompt,
                cwd,
                extra_args=extra_args,
                timeout_sec=timeout_sec,
                resume_session_id=resume_session_id,
                resume_last=resume_last,
                json_events=json_events,
                context_briefing=context_briefing,
            )
            result = _build_result(proc, cwd, cmd, json_events=json_events, prompt=prompt)
            if session_name and result.get("session_id"):
                _set_session_name(session_name, result["session_id"], provider)
                if result.get("conversation"):
                    _append_history(result["session_id"], session_name, result["conversation"])
            if session_name:
                result["session_name"] = session_name
                result["provider"] = provider
            status = 200 if result["returncode"] == 0 else 500
            return jsonify(result), status
        if not prompt or not isinstance(prompt, str):
            return jsonify({"error": "prompt must be a non-empty string"}), 400
        config = _get_provider_config()
        if provider == "copilot":
            proc, cmd = _run_copilot_exec(
                prompt,
                cwd,
                config=config,
                extra_args=extra_args,
                timeout_sec=timeout_sec,
                resume_session_id=resume_session_id,
                resume_last=resume_last,
                context_briefing=context_briefing,
            )
            text = _strip_copilot_footer((proc.stdout or "").strip())
            events = _build_synthetic_events(text)
            session_id = resume_session_id or _ensure_session_id(session_name, provider) if session_name else None
            result = {
                "returncode": proc.returncode,
                "stdout": text,
                "stderr": proc.stderr,
                "cwd": cwd,
                "cmd": cmd,
            }
        elif provider == "gemini":
            history_messages = _get_history_for_name(session_name).get("messages") if session_name else []
            if resume_session_id and resume_session_id.startswith("gemini-") and not resume_last:
                # Gemini resumes via --resume latest only when there is actual history.
                resume_last = _session_has_history(session_name, "gemini")
                resume_session_id = None
            text = _run_gemini_exec(prompt, history_messages, config=config, timeout_sec=timeout_sec, cwd=cwd, resume_session_id=resume_session_id, resume_last=resume_last, context_briefing=context_briefing)
            gemini_path = _resolve_gemini_path(config) or "gemini"
            events = _build_synthetic_events(text)
            session_id = resume_session_id or _ensure_session_id(session_name, provider) if session_name else None
            result = {
                "returncode": 0,
                "stdout": text,
                "stderr": "",
                "cwd": cwd,
                "cmd": [gemini_path, "-p", prompt],
            }
        elif provider == "claude":
            claude_start_time = time.time()
            session_id = resume_session_id if _is_uuid(resume_session_id) else None
            text = _run_claude_exec(
                prompt,
                config=config,
                timeout_sec=timeout_sec,
                cwd=cwd,
                resume_session_id=resume_session_id,
                resume_last=resume_last,
                context_briefing=context_briefing,
            )
            claude_path = _resolve_claude_path(config) or "claude"
            events = _build_synthetic_events(text)

            # Get actual Claude session ID from temp directory
            # Only extract if we don't already have a real Claude UUID
            if session_name and not _is_uuid(resume_session_id):
                search_dir = cwd if cwd else os.getcwd()
                actual_session_id = _wait_for_claude_session_id(
                    search_dir,
                    timeout_sec=3.0,
                    min_mtime=None,
                    exact_only=True,
                )
                if actual_session_id:
                    session_id = actual_session_id
                    _set_session_name(session_name, session_id, provider)
                    logger.info(f"Captured Claude session ID for {session_name}: {session_id}")
            elif _is_uuid(resume_session_id):
                session_id = resume_session_id

            result = {
                "returncode": 0,
                "stdout": text,
                "stderr": "",
                "cwd": cwd,
                "cmd": [claude_path, "--dangerously-skip-permissions", "<stdin>"],
            }
        else:
            return _error_response("unknown provider", code=ERR_UNKNOWN_PROVIDER, status=400)
        if json_events:
            result["events"] = events
            result["session_id"] = session_id
            result["thread_id"] = session_id
            result["conversation"] = _events_to_conversation(events, prompt=prompt)
        if session_name and session_id:
            _set_session_name(session_name, session_id, provider)
            if result.get("conversation"):
                _append_history(session_id, session_name, result["conversation"])
            result["session_name"] = session_name
            result["provider"] = provider
        status = 200 if result.get("returncode", 1) == 0 else 500
        return jsonify(result), status
    except subprocess.TimeoutExpired:
        return _error_response("codex exec timed out", code=ERR_TIMEOUT, status=504)
    except ValueError as exc:
        return _error_response(str(exc), code=ERR_INVALID_INPUT, status=400)
    except RuntimeError as exc:
        return _error_response(str(exc), code=ERR_CONFLICT, status=409)
    except FileNotFoundError:
        return _error_response("CLI not found in PATH", code=ERR_CLI_NOT_FOUND, status=500)
    finally:
        if session_name:
            _set_session_status(session_name, "idle")


class _Job:
    def __init__(
        self,
        key,
        session_name,
        prompt,
        cwd,
        extra_args,
        timeout_sec,
        resume_session_id,
        resume_last,
        json_events,
        provider,
        context_briefing=None,
    ):
        self.key = key
        self.session_name = session_name
        self.prompt = prompt
        self.cwd = cwd
        self.extra_args = extra_args
        self.timeout_sec = timeout_sec
        self.resume_session_id = resume_session_id
        self.resume_last = resume_last
        self.json_events = json_events
        self.provider = provider
        self.context_briefing = context_briefing
        self.session_id = resume_session_id
        self.subscribers = set()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.returncode = None
        self.buffer = deque(maxlen=800)
        self.finish_time = None  # Set when job completes for cleanup tracking

    def add_subscriber(self, q):
        with self.lock:
            self.subscribers.add(q)

    def remove_subscriber(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def broadcast(self, payload):
        with self.lock:
            self.buffer.append(payload)
            subscribers = list(self.subscribers)

        dead = []
        for q in subscribers:
            try:
                # Give slow clients 50ms to drain their queue
                q.put(payload, timeout=0.05)
            except queue.Full:
                logger.warning(f"[Backpressure] Disconnecting slow job subscriber for {self.session_name} (queue full)")
                dead.append(q)
            except Exception as e:
                logger.warning(f"[Backpressure] Error broadcasting to job subscriber: {e}")
                dead.append(q)

        # Remove dead subscribers
        if dead:
            with self.lock:
                for q in dead:
                    self.subscribers.discard(q)

    def add_subscriber_with_snapshot(self, q):
        with self.lock:
            self.subscribers.add(q)
            return list(self.buffer)


def _broadcast_agent_message(job, text):
    if not text:
        return
    evt = {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    job.broadcast(f"data: stdout:{json.dumps(evt)}\n\n")


def _enqueue_pending_prompt(session_name, payload):
    if not session_name or not payload:
        return
    with _PENDING_LOCK:
        queue = _PENDING_PROMPTS.setdefault(session_name, deque())
        queue.append(payload)


def _dequeue_pending_prompt(session_name):
    with _PENDING_LOCK:
        queue = _PENDING_PROMPTS.get(session_name)
        if queue:
            return queue.popleft()
    return None


def _start_next_pending(session_name):
    if not session_name:
        return
    payload = _dequeue_pending_prompt(session_name)
    if not payload:
        return
    provider = payload.get("provider") or _get_session_provider_for_name(session_name)
    resume_session_id = _get_session_id_for_name(session_name)
    job_key = f"{provider}:{session_name}"
    start_job = None
    with _JOB_LOCK:
        existing = _JOBS.get(job_key)
        if existing and not existing.done.is_set():
            # put it back if still running
            _enqueue_pending_prompt(session_name, payload)
            return
        job = _Job(
            job_key,
            session_name,
            payload.get("prompt"),
            payload.get("cwd"),
            payload.get("extra_args") or [],
            payload.get("timeout_sec") or 300,
            resume_session_id,
            bool(payload.get("resume_last", False)),
            bool(payload.get("json_events", True)),
            provider,
            context_briefing=payload.get("context_briefing"),
        )
        _JOBS[job_key] = job
        start_job = job
    if start_job:
        _set_session_status(session_name, "running")
        # Notify session viewers that status changed to running (show thinking indicator)
        _broadcast_session_message(session_name, {
            "type": "status_change",
            "status": "running"
        })
        _start_job(start_job)


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


def _start_job(job):
    _log_event(
        {
            "type": "job.start",
            "provider": job.provider,
            "session_name": job.session_name,
            "session_id": job.session_id,
            "prompt": job.prompt,
        }
    )
    if job.provider == "codex":
        _start_codex_job(job)
    elif job.provider == "copilot":
        _start_copilot_job(job)
    elif job.provider == "gemini":
        _start_gemini_job(job)
    elif job.provider == "claude":
        _start_claude_job(job)
    else:
        _broadcast_error(job, "unknown provider")
        job.finish_time = time.time()
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)


def _start_codex_job(job):
    def runner():
        codex_path = _resolve_codex_path()
        if not codex_path:
            _broadcast_error(job, "codex CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        if job.resume_session_id and str(job.resume_session_id).startswith("codex-"):
            # Synthetic IDs are for local tracking only; do not pass to codex resume.
            job.resume_session_id = None
        # _build_codex_args now returns (args, prompt) for stdin support
        args, prompt = _build_codex_args(
            codex_path,
            job.extra_args,
            job.json_events,
            job.resume_session_id,
            job.resume_last,
            job.prompt,
            job.context_briefing,
        )
        try:
            env = os.environ.copy()
            if not env.get("CODEX_HOME"):
                env["CODEX_HOME"] = _get_codex_home()
            if not env.get("GEMINI_API_KEY"):
                api_key = _get_gemini_api_key_from_settings(job.cwd)
                if api_key:
                    env["GEMINI_API_KEY"] = api_key
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
            # Write prompt to stdin for multi-line support
            proc.stdin.write(prompt + '\n')
            proc.stdin.close()
        except FileNotFoundError:
            _broadcast_error(job, "codex CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return

        q = queue.Queue()
        t_out = threading.Thread(target=_enqueue_output, args=(proc.stdout, q, "stdout"))
        t_err = threading.Thread(target=_enqueue_output, args=(proc.stderr, q, "stderr"))
        t_out.daemon = True
        t_err.daemon = True
        t_out.start()
        t_err.start()

        start = time.monotonic()
        sent_session = False
        assistant_chunks = []
        tool_outputs = []
        while True:
            try:
                label, line = q.get(timeout=0.25)
                line_text = line.rstrip("\n")
                if job.json_events and label == "stdout":
                    raw = line_text.strip()
                    try:
                        evt = json.loads(raw)
                        if not sent_session:
                            sess = _extract_session_id([evt])
                            if sess:
                                sent_session = True
                                old_id = job.session_id
                                job.session_id = sess
                                if job.session_name:
                                    _migrate_history_session_id(job.session_name, old_id, sess)
                                    _set_session_name(job.session_name, sess)
                                job.broadcast(f"event: session_id\ndata: {sess}\n\n")
                        if evt.get("type") == "item.completed":
                            item = evt.get("item") or {}
                            item_type = item.get("type")
                            if item_type == "agent_message":
                                text = item.get("text")
                                if isinstance(text, str) and text:
                                    assistant_chunks.append(text)
                            elif item_type == "command_execution":
                                output = item.get("aggregated_output") or ""
                                if output:
                                    tool_outputs.append(output)
                    except json.JSONDecodeError:
                        pass
                if label == "stderr" and "codex_core::rollout::list: state db missing rollout path for thread" in line_text:
                    continue
                job.broadcast(f"data: {label}:{line_text}\n\n")
            except queue.Empty:
                if proc.poll() is not None:
                    break
                if time.monotonic() - start > job.timeout_sec:
                    proc.kill()
                    _broadcast_error(job, "codex exec timed out")
                    break

        rc = proc.wait()

        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": rc,
            }
        )
        job.returncode = rc
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": tool_outputs}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                assistant_text = "\n".join(assistant_chunks).strip()
                conversation["messages"].append({"role": "assistant", "text": assistant_text})
            _append_history(job.session_id, job.session_name, conversation)

            # Note: Agent responses are already streamed via job SSE (/stream endpoint)
            # Don't broadcast to session viewers to avoid duplicates
            # BUT broadcast to master console for live updates (unless orchestrator is managing)
            if assistant_chunks and job.session_name:
                # Only broadcast if session doesn't have an orchestrator
                if not _session_has_orchestrator(job.session_name):
                    _broadcast_master_message(job.session_name, assistant_text)

                # Notify session viewers that new messages are available
                _broadcast_session_message(job.session_name, {
                    "type": "job_complete",
                    "session": job.session_name,
                    "has_response": True
                })

                # Trigger orchestrator check immediately (event-driven)
                _trigger_orchestrator_check(job.session_name)

        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
        job.finish_time = time.time()
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)
        _start_next_pending(job.session_name)
        _start_next_pending(job.session_name)
        _start_next_pending(job.session_name)
        _start_next_pending(job.session_name)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def _start_copilot_job(job):
    def runner():
        # Inject context briefing if provided
        prompt = job.prompt
        if job.context_briefing and not job.resume_session_id and not job.resume_last:
            logger.info(f"[Context] Injecting {len(job.context_briefing)} chars of context into copilot stream")
            prompt = f"""# Session Context

Previous conversation history from other providers:

{job.context_briefing}

---

# Current Request

{prompt}"""
        
        config = _get_provider_config()
        copilot_path = _resolve_copilot_path(config)
        if not copilot_path:
            _broadcast_error(job, "copilot CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        
        args = [copilot_path]

        # Add resume flag if resuming a session
        if job.resume_session_id and _is_uuid(job.resume_session_id):
            args.extend(["--resume", job.resume_session_id])
        elif job.resume_last:
            args.append("--continue")

        # Add permission flags based on config
        copilot_permissions = (config.get("copilot_permissions") or "").strip()
        if copilot_permissions:
            args.append(f"--{copilot_permissions}")
        else:
            args.append("--allow-all-paths")

        # Add model flag if configured
        copilot_model = (config.get("copilot_model") or "").strip()
        if copilot_model:
            args.extend(["--model", copilot_model])

        # Use stdin for multi-line prompt support (non-interactive)

        mcp_data = _load_mcp_json(config)
        if mcp_data and (config.get("copilot_enable_mcp") is True):
            mcp_path = _write_mcp_json_file(mcp_data)
            args.extend(["--additional-mcp-config", f"@{mcp_path}"])
        if job.extra_args:
            args.extend(job.extra_args)
        env = os.environ.copy()
        token = (config.get("copilot_token") or "").strip()
        token_env = (config.get("copilot_token_env") or "GH_TOKEN").strip() or "GH_TOKEN"
        if token:
            env[token_env] = token
        session_id = job.session_id or (job.session_name and _ensure_session_id(job.session_name, job.provider))
        if session_id and not _is_uuid(session_id):
            session_id = None
        if session_id:
            job.session_id = session_id
            job.broadcast(f"event: session_id\ndata: {session_id}\n\n")
        session_state_dir = pathlib.Path.home() / ".copilot" / "session-state"
        try:
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
            if prompt:
                proc.stdin.write(prompt + '\n')
                proc.stdin.flush()
                proc.stdin.close()
        except FileNotFoundError:
            _broadcast_error(job, "copilot CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return

        q = queue.Queue()
        t_out = threading.Thread(target=_enqueue_output, args=(proc.stdout, q, "stdout"))
        t_err = threading.Thread(target=_enqueue_output, args=(proc.stderr, q, "stderr"))
        t_out.daemon = True
        t_err.daemon = True
        t_out.start()
        t_err.start()

        start = time.monotonic()
        assistant_chunks = []
        suppress_footer = False
        while True:
            try:
                label, line = q.get(timeout=0.25)
                line_text = line.rstrip("\n")
                if label == "stdout":
                    if line_text:
                        if line_text.strip().startswith("Total usage est:"):
                            suppress_footer = True
                        if suppress_footer or _is_copilot_footer_line(line_text):
                            continue
                        assistant_chunks.append(line_text)
                        _broadcast_agent_message(job, line_text)
                else:
                    if line_text:
                        if suppress_footer or _is_copilot_footer_line(line_text):
                            continue
                        job.broadcast(f"data: {label}:{line_text}\n\n")
            except queue.Empty:
                if proc.poll() is not None:
                    break
                if time.monotonic() - start > job.timeout_sec:
                    proc.kill()
                    _broadcast_error(job, "copilot exec timed out")
                    break

        rc = proc.wait()

        # Capture Copilot session ID from session-state directory
        if job.session_name:
            session_state_dir = pathlib.Path.home() / ".copilot" / "session-state"
            if session_state_dir.exists():
                try:
                    candidates = []
                    for p in session_state_dir.iterdir():
                        if not p.is_dir():
                            continue
                        try:
                            mtime = p.stat().st_mtime
                        except (OSError, PermissionError) as e:
                            logger.debug(f"Cannot stat Copilot session dir {p.name}: {e}")
                            continue
                        candidates.append((mtime, p.name))
                    if candidates:
                        candidates.sort(reverse=True)
                        actual_id = candidates[0][1]
                        _set_session_name(job.session_name, actual_id, job.provider)
                        job.session_id = actual_id
                        job.broadcast(f"event: session_id\ndata: {actual_id}\n\n")
                        _log_event(
                            {
                                "type": "copilot.session_id",
                                "session_name": job.session_name,
                                "session_id": actual_id,
                            }
                        )
                except Exception as exc:
                    _log_event(
                        {
                            "type": "copilot.session_id_error",
                            "session_name": job.session_name,
                            "error": str(exc),
                        }
                    )

        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": rc,
            }
        )
        job.returncode = rc
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                assistant_text = "\n".join(assistant_chunks).strip()
                conversation["messages"].append({"role": "assistant", "text": assistant_text})
            logger.info(f"[Copilot History] Appending to session_id={job.session_id}, session_name={job.session_name}, messages={len(conversation['messages'])}")
            _append_history(job.session_id, job.session_name, conversation)

            # Note: Agent responses are already streamed via job SSE (/stream endpoint)
            # Don't broadcast to session viewers to avoid duplicates
            # BUT broadcast to master console for live updates (unless orchestrator is managing)
            if assistant_chunks and job.session_name:
                # Only broadcast if session doesn't have an orchestrator
                if not _session_has_orchestrator(job.session_name):
                    _broadcast_master_message(job.session_name, assistant_text)

                # Notify session viewers that new messages are available
                _broadcast_session_message(job.session_name, {
                    "type": "job_complete",
                    "session": job.session_name,
                    "has_response": True
                })

                # Trigger orchestrator check immediately (event-driven)
                _trigger_orchestrator_check(job.session_name)

        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
        job.finish_time = time.time()
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def _start_gemini_job(job):
    def runner():
        # Inject context briefing if provided
        prompt = job.prompt
        if job.context_briefing and not job.resume_session_id and not job.resume_last:
            logger.info(f"[Context] Injecting {len(job.context_briefing)} chars of context into gemini stream")
            prompt = f"""# Session Context

Previous conversation history from other providers:

{job.context_briefing}

---

# Current Request

{prompt}"""
        
        config = _get_provider_config()
        gemini_path = _resolve_gemini_path(config)
        if not gemini_path:
            _broadcast_error(job, "gemini CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        if not job.session_id and job.session_name:
            try:
                job.session_id = _get_session_id_for_name(job.session_name)
            except Exception as e:
                logger.warning(f"Cannot get session ID for {job.session_name}: {e}")
        if job.session_id:
            job.broadcast(f"event: session_id\ndata: {job.session_id}\n\n")
        
        args = [gemini_path]

        # Add resume flag if resuming a session (must come before -p)
        # Gemini manages its own session IDs, so we always use 'latest' to continue
        # Only resume if explicitly requested via resume_last flag
        if job.resume_last:
            args.extend(["--resume", "latest"])
        
        try:
            env = os.environ.copy()
            if not env.get("GEMINI_API_KEY"):
                api_key = _get_gemini_api_key_from_settings(job.cwd)
                if api_key:
                    env["GEMINI_API_KEY"] = api_key
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
            # Write prompt to stdin for multi-line support
            if prompt:
                proc.stdin.write(prompt + "\n")
                proc.stdin.flush()
                proc.stdin.close()
        except FileNotFoundError:
            _broadcast_error(job, "gemini CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return

        q = queue.Queue()
        t_out = threading.Thread(target=_enqueue_output, args=(proc.stdout, q, "stdout"))
        t_err = threading.Thread(target=_enqueue_output, args=(proc.stderr, q, "stderr"))
        t_out.daemon = True
        t_err.daemon = True
        t_out.start()
        t_err.start()

        assistant_chunks = []
        start = time.monotonic()
        while True:
            try:
                label, line = q.get(timeout=0.25)
                line_text = line.rstrip("\n")
                if label == "stdout":
                    if line_text:
                        assistant_chunks.append(line_text)
                        _broadcast_agent_message(job, line_text)
                else:
                    if line_text:
                        job.broadcast(f"data: {label}:{line_text}\n\n")
            except queue.Empty:
                if proc.poll() is not None:
                    break
                if time.monotonic() - start > job.timeout_sec:
                    proc.kill()
                    _broadcast_error(job, "gemini exec timed out")
                    break

        rc = proc.wait()
        job.returncode = rc
        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": rc,
            }
        )
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                assistant_text = "\n".join(assistant_chunks).strip()
                conversation["messages"].append({"role": "assistant", "text": assistant_text})
            _append_history(job.session_id, job.session_name, conversation)

            # Note: Agent responses are already streamed via job SSE (/stream endpoint)
            # Don't broadcast to session viewers to avoid duplicates
            # BUT broadcast to master console for live updates (unless orchestrator is managing)
            if assistant_chunks and job.session_name:
                # Only broadcast if session doesn't have an orchestrator
                if not _session_has_orchestrator(job.session_name):
                    _broadcast_master_message(job.session_name, assistant_text)

                # Notify session viewers that new messages are available
                _broadcast_session_message(job.session_name, {
                    "type": "job_complete",
                    "session": job.session_name,
                    "has_response": True
                })

                # Trigger orchestrator check immediately (event-driven)
                _trigger_orchestrator_check(job.session_name)

        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
        job.finish_time = time.time()
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def _start_claude_job(job):
    def runner():
        # Inject context briefing if provided
        prompt = job.prompt
        if job.context_briefing and not job.resume_session_id and not job.resume_last:
            logger.info(f"[Context] Injecting {len(job.context_briefing)} chars of context into claude stream")
            prompt = f"""# Session Context

Previous conversation history from other providers:

{job.context_briefing}

---

# Current Request

{prompt}"""
        
        config = _get_provider_config()
        claude_start_time = time.time()
        claude_path = _resolve_claude_path(config)
        if not claude_path:
            _broadcast_error(job, "claude CLI not found in PATH")
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        session_id = job.session_id or (job.session_name and _ensure_session_id(job.session_name, job.provider))
        if session_id:
            job.session_id = session_id
            job.broadcast(f"event: session_id\ndata: {session_id}\n\n")
        
        def run_claude_stream(args, prompt_text):
            try:
                env = os.environ.copy()
                if not env.get("GEMINI_API_KEY"):
                    api_key = _get_gemini_api_key_from_settings(job.cwd)
                    if api_key:
                        env["GEMINI_API_KEY"] = api_key
                proc = subprocess.Popen(
                    args,
                    cwd=job.cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    env=env,
                )
                if prompt_text:
                    proc.stdin.write(prompt_text + "\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except FileNotFoundError:
                _broadcast_error(job, "claude CLI not found in PATH")
                return None, [], []

            q = queue.Queue()
            t_out = threading.Thread(target=_enqueue_output, args=(proc.stdout, q, "stdout"))
            t_err = threading.Thread(target=_enqueue_output, args=(proc.stderr, q, "stderr"))
            t_out.daemon = True
            t_err.daemon = True
            t_out.start()
            t_err.start()

            assistant_chunks = []
            stderr_lines = []
            start = time.monotonic()
            while True:
                try:
                    label, line = q.get(timeout=0.25)
                    line_text = line.rstrip("\n")
                    if label == "stdout":
                        if line_text:
                            assistant_chunks.append(line_text)
                            _broadcast_agent_message(job, line_text)
                    else:
                        if line_text:
                            stderr_lines.append(line_text)
                            job.broadcast(f"data: {label}:{line_text}\n\n")
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    if time.monotonic() - start > job.timeout_sec:
                        proc.kill()
                        _broadcast_error(job, "claude exec timed out")
                        break

            proc.wait()
            return proc, assistant_chunks, stderr_lines

        args = [claude_path, "--dangerously-skip-permissions"]
        if _is_uuid(job.resume_session_id):
            args.extend(["--resume", job.resume_session_id])
        elif _is_uuid(job.session_id):
            args.extend(["--session-id", job.session_id])
        elif job.resume_last:
            args.append("--continue")

        proc, assistant_chunks, stderr_lines = run_claude_stream(args, prompt)
        if proc is None:
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return

        if proc.returncode and proc.returncode != 0:
            combined = "\n".join(stderr_lines) or "\n".join(assistant_chunks) or "claude CLI failed"
            _broadcast_error(job, _filter_debug_messages(combined).strip())
            job.returncode = proc.returncode
            job.finish_time = time.time()
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return

        # Extract actual Claude session ID from temp directory if session ID is not a valid UUID
        # (i.e., it's a generated ID that needs to be replaced with the real one)
        if job.session_name and not _is_uuid(job.session_id):
            search_dir = job.cwd if job.cwd else os.getcwd()
            actual_session_id = _wait_for_claude_session_id(
                search_dir,
                timeout_sec=3.0,
                min_mtime=None,
                exact_only=True,
            )
            if actual_session_id:
                job.session_id = actual_session_id
                _set_session_name(job.session_name, actual_session_id, job.provider)
                logger.info(f"Captured Claude session ID for {job.session_name}: {actual_session_id}")
                job.broadcast(f"event: session_id\ndata: {actual_session_id}\n\n")

        rc = 0  # Claude already checked for errors above, so if we reach here, rc is 0
        job.returncode = rc
        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": rc,
            }
        )
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                assistant_text = "\n".join(assistant_chunks).strip()
                conversation["messages"].append({"role": "assistant", "text": assistant_text})
            _append_history(job.session_id, job.session_name, conversation)

            # Note: Agent responses are already streamed via job SSE (/stream endpoint)
            # Don't broadcast to session viewers to avoid duplicates
            # BUT broadcast to master console for live updates (unless orchestrator is managing)
            if assistant_chunks and job.session_name:
                # Only broadcast if session doesn't have an orchestrator
                if not _session_has_orchestrator(job.session_name):
                    _broadcast_master_message(job.session_name, assistant_text)

                # Notify session viewers that new messages are available
                _broadcast_session_message(job.session_name, {
                    "type": "job_complete",
                    "session": job.session_name,
                    "has_response": True
                })

                # Trigger orchestrator check immediately (event-driven)
                _trigger_orchestrator_check(job.session_name)

        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
        job.finish_time = time.time()
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


def _run_task_exec(task):
    prompt = (task.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("task prompt is empty")
    provider = (task.get("provider") or DEFAULT_PROVIDER).lower()
    config = _get_provider_config()
    cwd = _safe_cwd((task.get("workdir") or "").strip() or None)

    # Get task timeout (default 900s, increased from 300s for complex tasks)
    timeout_sec = task.get("timeout_sec", 900)

    # For tasks, we need to ensure non-interactive execution
    if provider == "codex":
        # Override sandbox mode to danger-full-access for tasks
        original_sandbox = config.get("sandbox_mode_codex")
        config["sandbox_mode_codex"] = "danger-full-access"
        try:
            proc, cmd = _run_codex_exec(prompt, cwd, json_events=True, timeout_sec=timeout_sec)
        finally:
            # Restore original sandbox mode
            if original_sandbox is None:
                config.pop("sandbox_mode_codex", None)
            else:
                config["sandbox_mode_codex"] = original_sandbox

        if proc.returncode != 0:
            error_msg = _filter_debug_messages(proc.stderr or proc.stdout or "codex failed").strip()
            raise RuntimeError(error_msg)
        # Parse JSON events to get the output
        raw_output = _filter_debug_messages((proc.stdout or "").strip())
        output_text = _extract_codex_assistant_output(raw_output)
        return {"output": output_text, "raw_output": raw_output, "cmd": cmd}
    if provider == "copilot":
        proc, cmd = _run_copilot_exec(prompt, cwd, config=config, timeout_sec=timeout_sec)
        if proc.returncode != 0:
            error_msg = _filter_debug_messages(proc.stderr or proc.stdout or "copilot failed").strip()
            raise RuntimeError(error_msg)
        raw_output = _filter_debug_messages(_strip_copilot_footer((proc.stdout or "").strip()))
        return {"output": raw_output, "raw_output": raw_output, "cmd": cmd}
    if provider == "gemini":
        text = _run_gemini_exec(prompt, [], config=config, cwd=cwd, timeout_sec=timeout_sec)
        return {"output": text, "raw_output": text, "cmd": [_resolve_gemini_path(config) or "gemini", "-p", prompt]}
    if provider == "claude":
        text = _run_claude_exec(prompt, config=config, cwd=cwd, timeout_sec=timeout_sec)
        return {
            "output": text,
            "raw_output": text,
            "cmd": [_resolve_claude_path(config) or "claude", "--dangerously-skip-permissions", "<stdin>"],
        }
    raise RuntimeError("unknown provider")


def _mark_task_run(task_id, status, output=None, raw_output=None, error=None, runtime_sec=None, started_at=None):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    started_at = started_at or now
    with _TASK_LOCK:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return
        task["last_run"] = now
        task["last_status"] = status
        task["last_runtime_sec"] = runtime_sec
        if output is not None:
            task["last_output"] = output
        if raw_output is not None:
            task["last_output_raw"] = raw_output
        if error is not None:
            task["last_error"] = error
        elif status == "ok":
            # Clear error on successful run
            task["last_error"] = None
        run_history = task.get("run_history")
        if not isinstance(run_history, list):
            run_history = []
        run_history.append(
            {
                "started_at": started_at,
                "finished_at": now,
                "runtime_sec": runtime_sec,
                "status": status,
                "output": output or "",
                "raw_output": raw_output or "",
                "error": error,
            }
        )
        # Limit run_history to prevent unbounded growth (same as orchestrators)
        if len(run_history) > 200:
            run_history = run_history[-200:]
        task["run_history"] = run_history
        task["next_run"] = None
        if task.get("enabled"):
            next_dt = _compute_next_run(task)
            task["next_run"] = next_dt.isoformat(timespec="seconds") if next_dt else None
        tasks[task_id] = task
        _save_tasks(tasks)
    _broadcast_tasks_snapshot()


def _run_task_async(task_id, force_run=False):
    def runner():
        started_ts = time.time()
        started_at = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with _TASK_LOCK:
                tasks = _load_tasks()
                task = tasks.get(task_id)
                if task:
                    task["last_status"] = "running"
                    _save_tasks(tasks)
            _broadcast_tasks_snapshot()
            if task:
                _task_stream_publish(task_id, "status", {"status": "running"})

            if not task:
                return

            # Failsafe: If task was disabled during execution, return to idle (unless manual run)
            if not task.get("enabled") and not force_run:
                with _TASK_LOCK:
                    tasks = _load_tasks()
                    if task_id in tasks:
                        tasks[task_id]["last_status"] = "idle"
                        _save_tasks(tasks)
                _broadcast_tasks_snapshot()
                return

            provider = (task.get("provider") or DEFAULT_PROVIDER).lower()
            live_chunks = []

            def on_output(line):
                live_chunks.append(line)
                _task_stream_publish(task_id, "output", {"text": line})

            def on_error(line):
                _task_stream_publish(task_id, "stderr", {"text": line})

            if provider == "gemini":
                text = _run_gemini_exec_stream(
                    (task.get("prompt") or "").strip(),
                    config=_get_provider_config(),
                    cwd=_safe_cwd((task.get("workdir") or "").strip() or None),
                    timeout_sec=task.get("timeout_sec", 900),
                    on_output=on_output,
                    on_error=on_error,
                )
                result = {"output": text, "raw_output": text}
            elif provider == "claude":
                text = _run_claude_exec_stream(
                    (task.get("prompt") or "").strip(),
                    config=_get_provider_config(),
                    cwd=_safe_cwd((task.get("workdir") or "").strip() or None),
                    timeout_sec=task.get("timeout_sec", 900),
                    on_output=on_output,
                    on_error=on_error,
                )
                result = {"output": text, "raw_output": text}
            else:
                result = _run_task_exec(task)
            runtime_sec = time.time() - started_ts
            _mark_task_run(
                task_id,
                "ok",
                output=result.get("output") or "",
                raw_output=result.get("raw_output"),
                runtime_sec=runtime_sec,
                started_at=started_at,
            )
            _task_stream_publish(task_id, "done", {"status": "ok"})
        except Exception as exc:
            runtime_sec = time.time() - started_ts
            _mark_task_run(task_id, "error", error=str(exc), runtime_sec=runtime_sec, started_at=started_at)
            _task_stream_publish(task_id, "done", {"status": "error", "error": str(exc)})

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


class _StreamHistory:
    """Maintains message history for SSE stream reconnection.

    Stores recent messages with incrementing IDs to support Last-Event-ID
    based reconnection and replay of missed messages.
    """
    def __init__(self, maxlen=1000):
        """Initialize stream history buffer.

        Args:
            maxlen: Maximum number of messages to keep in buffer
        """
        self.messages = deque(maxlen=maxlen)
        self.counter = 0
        self.lock = threading.Lock()

    def add(self, payload):
        """Add a message to the history buffer.

        Args:
            payload: The message payload to store

        Returns:
            int: The message ID assigned to this message
        """
        with self.lock:
            self.counter += 1
            self.messages.append((self.counter, payload))
            return self.counter

    def replay_from(self, last_id):
        """Get all messages after a given ID for reconnection replay.

        Args:
            last_id: The last message ID the client received

        Returns:
            list: List of (message_id, payload) tuples after last_id
        """
        with self.lock:
            return [(mid, p) for mid, p in self.messages if mid > last_id]


# Stream history buffers for reconnection support
_SESSION_STREAM_HISTORY = _StreamHistory(maxlen=1000)
_MASTER_STREAM_HISTORY = _StreamHistory(maxlen=1000)
_TASK_STREAM_HISTORY = _StreamHistory(maxlen=1000)
_SESSION_MESSAGE_HISTORY = {}  # session_name -> _StreamHistory


def _cleanup_dead_subscribers():
    """Remove queues that are no longer being read from subscriber sets.

    This prevents memory leaks from disconnected clients whose queues
    remain in the subscriber sets indefinitely.
    """
    cleaned_count = 0

    # Check each subscriber set
    for subscriber_set in [_SESSION_SUBSCRIBERS, _TASK_SUBSCRIBERS, _MASTER_SUBSCRIBERS]:
        dead = []
        for q in list(subscriber_set):
            try:
                # If queue is full and not being drained, it's likely dead
                if q.full() and q.qsize() >= q.maxsize:
                    dead.append(q)
            except Exception:
                # Queue is in a bad state, mark for removal
                dead.append(q)

        for q in dead:
            subscriber_set.discard(q)
            cleaned_count += 1

    # Check session viewers (nested dict structure)
    for session_name in list(_SESSION_VIEWERS.keys()):
        viewers = _SESSION_VIEWERS.get(session_name, set())
        dead = []
        for q in list(viewers):
            try:
                if q.full() and q.qsize() >= q.maxsize:
                    dead.append(q)
            except Exception:
                dead.append(q)

        for q in dead:
            viewers.discard(q)
            cleaned_count += 1

        # Remove empty viewer sets
        if not viewers:
            _SESSION_VIEWERS.pop(session_name, None)

    if cleaned_count > 0:
        logger.info(f"[Cleanup] Removed {cleaned_count} dead subscriber queue(s)")

    return cleaned_count


def _cleanup_old_jobs():
    """Remove completed jobs older than 1 hour to prevent memory leaks.

    Each job keeps up to 800 messages in its buffer. Old completed jobs
    accumulate in memory indefinitely without cleanup.
    """
    cutoff = time.time() - 3600  # 1 hour ago
    cleaned_count = 0

    with _JOB_LOCK:
        dead_keys = []
        for k, job in _JOBS.items():
            # Only clean up jobs that are done
            if not job.done.is_set():
                continue

            # Check if job has a finish timestamp
            finish_time = getattr(job, 'finish_time', None)
            if finish_time is None:
                # Job is done but no timestamp - estimate from current time
                # This handles jobs completed before we added timestamp tracking
                finish_time = time.time() - 1800  # Assume 30 min ago

            if finish_time < cutoff:
                dead_keys.append(k)

        for k in dead_keys:
            _JOBS.pop(k, None)
            cleaned_count += 1

    if cleaned_count > 0:
        logger.info(f"[Cleanup] Removed {cleaned_count} old job(s)")

    return cleaned_count


def _task_scheduler_loop():
    while True:
        now = datetime.datetime.now()
        due = []
        with _TASK_LOCK:
            tasks = _load_tasks()
        for task_id, task in tasks.items():
            if not task.get("enabled"):
                continue
            next_run = task.get("next_run")
            if not next_run:
                next_dt = _compute_next_run(task, now=now)
                if next_dt:
                    task["next_run"] = next_dt.isoformat(timespec="seconds")
                    with _TASK_LOCK:
                        tasks = _load_tasks()
                        if task_id in tasks:
                            tasks[task_id]["next_run"] = task["next_run"]
                            _save_tasks(tasks)
                    _broadcast_tasks_snapshot()
                continue
            try:
                next_dt = datetime.datetime.fromisoformat(next_run)
            except ValueError:
                next_dt = None
            if next_dt and next_dt <= now:
                due.append(task_id)
        for task_id in due:
            _run_task_async(task_id)

        # Periodic cleanup to prevent memory leaks
        _cleanup_dead_subscribers()
        _cleanup_old_jobs()

        time.sleep(30)


def _trigger_orchestrator_check(session_name):
    """Trigger an orchestrator check for a specific session.

    This is called when a job completes to immediately check if orchestrators
    need to make decisions, instead of waiting for the polling loop.

    Args:
        session_name: The session that just completed
    """
    if not session_name:
        return

    # Add to trigger queue (non-blocking)
    try:
        _ORCH_TRIGGER_QUEUE.put_nowait(session_name)
    except queue.Full:
        # Queue full, orchestrator will pick it up in polling loop
        pass


def _process_orchestrator_session(orch_id, orch, session_name, state):
    """Process a single orchestrator session.

    Extracted from the orchestrator loop to be reusable for event-driven triggers.

    Args:
        orch_id: Orchestrator ID
        orch: Orchestrator dict
        session_name: Session to process
        state: Orchestrator state dict for this orchestrator

    Returns:
        Updated state entry for the session
    """
    status = _get_session_status(session_name)
    entry = state.get(session_name) or {"status": None, "handled_idle": False, "last_output_idx": -1}
    prev = entry.get("status")

    entry["status"] = status
    if status == "running":
        entry["handled_idle"] = False

    should_handle = False
    if prev == "running" and status == "idle":
        should_handle = True
    elif prev is None and status == "idle" and not entry.get("handled_idle"):
        should_handle = True

    if not should_handle:
        return entry

    print(f"[Orchestrator] Session '{session_name}' needs handling (prev={prev}, status={status})")
    history = _get_history_for_name(session_name)
    has_history = bool(history.get("messages"))

    # Kickoff if no history
    if not has_history and not entry.get("kickoff_sent"):
        print(f"[Orchestrator] Preparing kickoff for '{session_name}'")
        kickoff_already = False
        for h in (orch.get("history") or []):
            if h.get("action") == "kickoff" and h.get("target_session") == session_name:
                kickoff_already = True
                break

        if kickoff_already:
            entry["kickoff_sent"] = True
            entry["handled_idle"] = True
            return entry

        role = _infer_worker_role(orch.get("goal") or "")
        config = _load_client_config()
        kickoff_template = _get_orchestrator_worker_prompt(config)
        session_workdir = _get_session_workdir(session_name)
        kickoff = _build_worker_kickoff_prompt(
            orch.get("goal") or "",
            role,
            kickoff_template,
            session_workdir,
        )
        _inject_prompt_to_session(session_name, kickoff)
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        _append_orchestrator_history(
            orch_id,
            orch,
            {
                "at": now_iso,
                "action": "kickoff",
                "target_session": session_name,
                "prompt": kickoff,
                "question": "",
                "raw": "",
            },
        )
        entry["kickoff_sent"] = True
        entry["handled_idle"] = True
        entry["last_output"] = None
        entry["last_output_idx"] = -1
        return entry

    # Check for new output and make decision
    latest_idx, latest = _get_latest_assistant_message_with_index(session_name)
    if latest_idx < 0 or latest_idx == entry.get("last_output_idx") or not latest:
        print(f"[Orchestrator]   Skipping - no new output")
        entry["handled_idle"] = True
        return entry

    print(f"[Orchestrator]   Making decision for new output...")
    action = _run_orchestrator_decision(orch, session_name, latest)
    if not action or not isinstance(action, dict):
        return entry

    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    action_type = action.get("action") or ""

    if action_type not in {"continue", "done", "ask_human"}:
        logger.warning(f"[Orchestrator] Invalid action '{action_type}' from {orch.get('name')} for session '{session_name}', using 'done' instead")
        action_type = "done"
        action = {
            "action": "done",
            "target_session": session_name,
            "message": "",
            "question": "",
            "raw": action.get("raw") or action.get("_raw") or "",
        }

    # Handle actions (ask_human, continue, done)
    if action_type == "ask_human":
        question = action.get("question") or ""
        if question:
            if "target_session" not in action:
                action["target_session"] = session_name

            # Store pending question and wait for human response
            # Do NOT inject into session - that causes infinite loops
            with _ORCH_LOCK:
                data = _load_orchestrators()
                current = data.get(orch_id) or orch
                current["pending_question"] = {
                    "question": question,
                    "target_session": session_name,
                    "asked_at": now_iso
                }
                data[orch_id] = current
                _save_orchestrators(data)

            # Broadcast to master console for visibility
            _broadcast_master_message(session_name, {
                "type": "orchestrator_question",
                "session_name": session_name,
                "orchestrator_id": orch_id,
                "question": question
            })
    elif action_type == "continue":
        managed = orch.get("managed_sessions") or []
        message = action.get("message")
        if session_name in managed and message:
            _inject_prompt_to_session(session_name, message)
            # Broadcast using session name, not orchestrator name, so user knows which session
            _broadcast_master_message(session_name, message)
    elif action_type == "done":
        goal = orch.get("goal") or ""
        completion_msg = f"[Orchestrator] {orch.get('name', 'Orchestrator')} completed work on '{session_name}'"
        completion_msg += f"\n\nGoal: {goal}"
        _broadcast_master_message(session_name, {
            "type": "orchestrator_completion",
            "session_name": session_name,
            "orchestrator_id": orch_id,
            "goal": goal
        })

    _append_orchestrator_history(orch_id, orch, action)
    entry["last_output_idx"] = latest_idx
    entry["last_output"] = latest
    entry["handled_idle"] = True

    with _ORCH_LOCK:
        data = _load_orchestrators()
        current = data.get(orch_id) or orch
        current["last_action"] = action_type
        current["last_decision_at"] = now_iso
        current["last_question"] = action.get("question") if action_type == "ask_human" else ""
        data[orch_id] = current
        _save_orchestrators(data)

    return entry


def _orchestrator_event_processor():
    """Process orchestrator triggers from the event queue.

    This runs in a background thread and processes session completion events
    immediately instead of waiting for the polling loop.
    """
    logger.info("[Orchestrator] Event processor starting...")
    while True:
        try:
            # Block until a trigger arrives
            session_name = _ORCH_TRIGGER_QUEUE.get(timeout=5)

            # Check if already processing this session
            with _ORCH_PROCESSING_LOCK:
                if session_name in _ORCH_PROCESSING:
                    continue  # Skip, already being processed
                _ORCH_PROCESSING[session_name] = True

            try:
                # Find orchestrators managing this session
                with _ORCH_LOCK:
                    orchestrators = _load_orchestrators()

                for orch_id, orch in orchestrators.items():
                    if not orch.get("enabled"):
                        continue
                    managed = orch.get("managed_sessions") or []
                    if session_name not in managed:
                        continue

                    print(f"[Orchestrator Event] Processing '{session_name}' for {orch.get('name', 'Unknown')}")
                    state = _ORCH_STATE.setdefault(orch_id, {})
                    entry = _process_orchestrator_session(orch_id, orch, session_name, state)
                    state[session_name] = entry

            finally:
                with _ORCH_PROCESSING_LOCK:
                    _ORCH_PROCESSING.pop(session_name, None)

        except queue.Empty:
            # No triggers in queue, continue waiting
            continue
        except Exception as exc:
            logger.error(f"[Orchestrator Event] Error processing trigger: {exc}")
            import traceback
            traceback.print_exc()


def _orchestrator_loop():
    """Polling loop for orchestrators (runs slowly as failsafe).

    Most orchestrator checks are now event-driven (triggered on job completion),
    but this loop runs every 30 seconds as a failsafe to catch any missed events.
    """
    logger.info("[Orchestrator] Polling loop starting (30s interval, event-driven primary)...")
    print("[Orchestrator] Polling loop starting (30s interval, event-driven primary)...")
    iteration = 0
    while True:
        try:
            iteration += 1
            with _ORCH_LOCK:
                orchestrators = _load_orchestrators()
            enabled_count = sum(1 for o in orchestrators.values() if o.get("enabled"))
            if orchestrators and iteration % 10 == 1:  # Log less frequently
                logger.info(f"[Orchestrator Poll] Checking {len(orchestrators)} orchestrator(s), {enabled_count} enabled...")
            for orch_id, orch in orchestrators.items():
                if not orch.get("enabled"):
                    continue
                managed = orch.get("managed_sessions") or []
                if not managed:
                    continue
                state = _ORCH_STATE.setdefault(orch_id, {})
                for name in managed:
                    # Use the extracted processing function
                    entry = _process_orchestrator_session(orch_id, orch, name, state)
                    state[name] = entry
        except Exception as exc:
            logger.error(f"[Orchestrator] loop error: {exc}")
            print(f"[Orchestrator] ERROR: {exc}")
            import traceback
            traceback.print_exc()
        time.sleep(30)  # Slow polling as failsafe (main work is event-driven)


_BACKGROUND_THREADS_STARTED = False
_BACKGROUND_THREADS_LOCK = threading.Lock()

# Event-driven orchestrator triggers
_ORCH_TRIGGER_QUEUE = queue.Queue()
_ORCH_PROCESSING = {}  # Track which sessions are currently being processed
_ORCH_PROCESSING_LOCK = threading.Lock()


def _ensure_background_threads_started():
    global _BACKGROUND_THREADS_STARTED
    if _BACKGROUND_THREADS_STARTED:
        return
    with _BACKGROUND_THREADS_LOCK:
        if _BACKGROUND_THREADS_STARTED:
            return
        logger.info("[Background] Starting task scheduler thread...")
        print("[Background] Starting task scheduler thread...")
        threading.Thread(target=_task_scheduler_loop, daemon=True).start()
        logger.info("[Background] Starting orchestrator event processor...")
        print("[Background] Starting orchestrator event processor...")
        threading.Thread(target=_orchestrator_event_processor, daemon=True).start()
        logger.info("[Background] Starting orchestrator polling loop (30s failsafe)...")
        print("[Background] Starting orchestrator polling loop (30s failsafe)...")
        threading.Thread(target=_orchestrator_loop, daemon=True).start()
        _BACKGROUND_THREADS_STARTED = True
        logger.info("[Background] All background threads started")
        print("[Background] All background threads started")


@APP.before_request
def _start_background_threads_once():
    _ensure_background_threads_started()


@APP.post("/stream")
def stream_codex():
    body, err = _require_json_body()
    if err:
        return err
    prompt = body.get("prompt")
    extra_args = body.get("extra_args") or []
    timeout_sec = body.get("timeout_sec", 300)
    resume_session_id = body.get("session_id")
    session_name = body.get("session_name")
    requested_provider = body.get("provider")
    resume_last = bool(body.get("resume_last", False))
    json_events = bool(body.get("json_events", True))
    attach = bool(body.get("attach", False))
    _log_event(
        {
            "type": "stream.request",
            "session_name": session_name,
            "provider": requested_provider,
            "has_prompt": bool(prompt),
        }
    )
    if session_name:
        name_err = _validate_name(session_name, "session_name")
        if name_err:
            return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
    if requested_provider:
        provider_err = _validate_provider(requested_provider)
        if provider_err:
            return _error_response(provider_err, code=ERR_INVALID_PROVIDER, status=400)
    if not attach and (not isinstance(prompt, str) or not prompt.strip()):
        return _error_response("prompt must be a non-empty string", code=ERR_INVALID_PROMPT, status=400)
    if not isinstance(extra_args, list) or not all(isinstance(x, str) for x in extra_args):
        return _error_response("extra_args must be a list of strings", code=ERR_INVALID_INPUT, status=400)
    if not isinstance(timeout_sec, int) or timeout_sec <= 0 or timeout_sec > 3600:
        return _error_response("timeout_sec must be an integer between 1 and 3600", code=ERR_INVALID_TIMEOUT, status=400)

    job_holder = {"job": None, "error": None}
    ready = threading.Event()

    def setup_job():
        try:
            cwd = _safe_cwd(body.get("cwd"))
            local_resume_last = resume_last
            current_provider_before = None
            current_session_id_before = None
            record = {}
            if session_name:
                # Avoid locking in the hot path; read sessions directly.
                data = _load_sessions()
                record = data.get(session_name) or {}
                # Use session's workdir if available (critical for Claude session lookup)
                session_workdir = record.get("workdir")
                if session_workdir:
                    cwd = _safe_cwd(session_workdir)
                current_provider_before = (record.get("provider") or DEFAULT_PROVIDER).lower()
                session_ids = record.get("session_ids") or {}
                if isinstance(session_ids, dict):
                    current_session_id_before = session_ids.get(current_provider_before)
            if not resume_session_id and session_name:
                session_ids = record.get("session_ids") or {}
                local_resume_id = session_ids.get((record.get("provider") or DEFAULT_PROVIDER).lower()) or record.get("session_id")
            else:
                local_resume_id = resume_session_id
            provider = (requested_provider or (record.get("provider") if session_name else None) or DEFAULT_PROVIDER).lower()
            if provider not in SUPPORTED_PROVIDERS:
                raise ValueError("unknown provider")
            if provider == "gemini" and local_resume_id and local_resume_id.startswith("gemini-") and not resume_last:
                local_resume_id = None
            if session_name:
                def _async_set_provider():
                    try:
                        _set_session_provider(session_name, provider)
                    except Exception as e:
                        logger.error(f"Failed to set provider for session {session_name} to {provider}: {e}", exc_info=True)
                threading.Thread(target=_async_set_provider, daemon=True).start()
            if session_name and provider == "gemini":
                try:
                    _ensure_session_id(session_name, provider)
                    record = _load_sessions().get(session_name) or record
                    if not local_resume_id:
                        session_ids = record.get("session_ids") or {}
                        local_resume_id = session_ids.get(provider) or record.get("session_id")
                except Exception as e:
                    logger.warning(f"Cannot ensure Gemini session ID for {session_name}: {e}")
            # Avoid blocking on disk writes in the stream setup path.
            if session_name:
                def _async_touch():
                    try:
                        _touch_session(session_name)
                    except Exception as e:
                        logger.error(f"Failed to touch session {session_name}: {e}", exc_info=True)
                threading.Thread(target=_async_touch, daemon=True).start()

            # Check if we're switching providers and need to generate context.
            # Do this asynchronously so stream setup never blocks on summary generation.
            if session_name and current_provider_before and provider != current_provider_before:
                with _SESSION_LOCK:
                    data = _load_sessions()
                    record = data.get(session_name) or {}
                    session_ids = record.get("session_ids") or {}
                    new_provider_session_id = session_ids.get(provider)
                    workdir = record.get("workdir")
                if not new_provider_session_id and current_session_id_before:
                    def _async_context_summary():
                        try:
                            config = _get_provider_config()
                            summary = _generate_session_summary(
                                current_provider_before,
                                current_session_id_before,
                                session_name,
                                config,
                                workdir,
                            )
                            _append_context_briefing(session_name, summary, current_provider_before, provider)
                        except Exception as e:
                            logger.error(f"Failed to generate context summary for {session_name}: {e}", exc_info=True)
                    t = threading.Thread(target=_async_context_summary, daemon=True)
                    t.start()

            # Load context briefing for new provider sessions (when provider just switched)
            context_briefing = None
            if session_name:
                session_ids = record.get("session_ids") or {}
                provider_has_session = session_ids.get(provider) if isinstance(session_ids, dict) else None
                if not provider_has_session:
                    context_briefing = _load_session_context(session_name)
                if provider == "gemini" and provider_has_session and not local_resume_last:
                    local_resume_last = _session_has_history(session_name, "gemini")
            job_key = f"{provider}:{session_name or local_resume_id or f'anon-{uuid.uuid4().hex}'}"
            job_to_start = None
            set_running = False
            with _JOB_LOCK:
                existing = _JOBS.get(job_key)
                if existing and not existing.done.is_set():
                    if attach or not prompt:
                        job = existing
                    else:
                        queued_payload = {
                            "prompt": prompt,
                            "provider": provider,
                            "cwd": cwd,
                            "extra_args": extra_args,
                            "timeout_sec": timeout_sec,
                            "resume_last": local_resume_last,
                            "json_events": json_events,
                            "context_briefing": context_briefing,
                        }
                        _enqueue_pending_prompt(session_name, queued_payload)
                        job_holder["error"] = "queued"
                        return
                else:
                    if not prompt or not isinstance(prompt, str):
                        job_holder["error"] = "prompt must be a non-empty string"
                        return
                    job = _Job(
                        job_key,
                        session_name,
                        prompt,
                        cwd,
                        extra_args,
                        timeout_sec,
                        local_resume_id,
                        local_resume_last,
                        json_events,
                        provider,
                        context_briefing=context_briefing,
                    )
                    _JOBS[job_key] = job
                    job_to_start = job
                    set_running = bool(session_name)
            if set_running:
                def _async_status():
                    try:
                        _set_session_status(session_name, "running")
                    except Exception as e:
                        logger.error(f"Failed to set status for session {session_name}: {e}", exc_info=True)
                threading.Thread(target=_async_status, daemon=True).start()
            if job_to_start:
                _start_job(job_to_start)
                job = job_to_start
            job_holder["job"] = job
        except ValueError as exc:
            job_holder["error"] = str(exc)
        except RuntimeError as exc:
            job_holder["error"] = str(exc)
        except Exception as exc:
            job_holder["error"] = str(exc)
        finally:
            ready.set()

    threading.Thread(target=setup_job, daemon=True).start()

    def generate():
        yield "event: open\ndata: {}\n\n"
        start = time.monotonic()
        while not ready.is_set():
            yield "event: ping\ndata: {}\n\n"
            time.sleep(0.5)
            if time.monotonic() - start > 10:
                yield "event: status\ndata: {\"status\":\"initializing\"}\n\n"
                start = time.monotonic()
        if job_holder.get("error") == "queued":
            evt = {"type": "item.completed", "item": {"type": "agent_message", "text": "Queued: message will run after the current response finishes."}}
            yield f"data: stdout:{json.dumps(evt)}\n\n"
            yield "event: done\ndata: queued=1\n\n"
            return
        if job_holder.get("error"):
            yield f"event: error\ndata: {job_holder.get('error')}\n\n"
            yield "event: done\ndata: error\n\n"
            return
        job = job_holder.get("job")
        subscriber = queue.Queue(maxsize=200)
        snapshot = job.add_subscriber_with_snapshot(subscriber)
        try:
            for payload in snapshot:
                yield payload
            while True:
                try:
                    payload = subscriber.get(timeout=0.5)
                    yield payload
                except queue.Empty:
                    if job.done.is_set():
                        break
                    # Keep SSE connection alive while waiting for first output.
                    yield "event: ping\ndata: {}\n\n"
        finally:
            job.remove_subscriber(subscriber)

    return Response(generate(), mimetype="text/event-stream; charset=utf-8")


@APP.get("/stream/health")
def stream_health():
    def generate():
        yield "event: open\ndata: {}\n\n"
        yield "event: done\ndata: ok\n\n"
    return Response(generate(), mimetype="text/event-stream; charset=utf-8")


@APP.get("/sessions")
def list_sessions():
    with _SESSION_LOCK:
        data = _load_sessions()
    status = _sessions_with_status(data)
    return jsonify({"count": len(data), "sessions": data, "status": status})


@APP.get("/sessions/stream")
def stream_sessions():
    def generate():
        q = queue.Queue(maxsize=100)
        _SESSION_SUBSCRIBERS.add(q)
        try:
            snapshot = _build_sessions_snapshot()
            yield f"data: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield f"data: {json.dumps({'type': 'snapshot', **payload})}\n\n"
                except queue.Empty:
                    # Send heartbeat to detect disconnected clients
                    yield ": heartbeat\n\n"
        finally:
            _SESSION_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@APP.get("/sessions/messages/stream")
def stream_session_messages():
    """Stream real-time messages for a specific session.

    Query params:
        session: The session name to subscribe to

    Streams events like:
        - Orchestrator prompt injections
        - User messages (future: collaborative editing)
        - Agent responses (future: real-time streaming)
        - Tool outputs
    """
    session_name = request.args.get("session", "").strip()
    if not session_name:
        return jsonify({"error": "session parameter required"}), 400

    # Check for reconnection with Last-Event-ID
    last_event_id = request.headers.get('Last-Event-ID')
    last_id = None
    if last_event_id:
        try:
            last_id = int(last_event_id)
        except (ValueError, TypeError):
            pass

    def generate():
        q = queue.Queue(maxsize=100)
        viewers = _SESSION_VIEWERS.setdefault(session_name, set())
        viewers.add(q)
        try:
            yield "event: open\ndata: {}\n\n"

            # Replay missed messages if reconnecting
            if last_id is not None and session_name in _SESSION_MESSAGE_HISTORY:
                missed = _SESSION_MESSAGE_HISTORY[session_name].replay_from(last_id)
                for msg_id, payload in missed:
                    yield f"id: {msg_id}\ndata: {json.dumps(payload)}\n\n"

            while True:
                try:
                    payload = q.get(timeout=15)
                    # Get message ID from session history
                    if session_name in _SESSION_MESSAGE_HISTORY:
                        msg_id = _SESSION_MESSAGE_HISTORY[session_name].counter
                        yield f"id: {msg_id}\ndata: {json.dumps(payload)}\n\n"
                    else:
                        yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    # Send heartbeat to detect disconnected clients
                    yield ": heartbeat\n\n"
        finally:
            viewers.discard(q)
            if not viewers:
                _SESSION_VIEWERS.pop(session_name, None)

    return Response(generate(), mimetype="text/event-stream")


@APP.get("/master/stream")
def stream_master():
    # Check for reconnection with Last-Event-ID
    last_event_id = request.headers.get('Last-Event-ID')
    last_id = None
    if last_event_id:
        try:
            last_id = int(last_event_id)
        except (ValueError, TypeError):
            pass

    def generate():
        q = queue.Queue(maxsize=100)
        _MASTER_SUBSCRIBERS.add(q)
        try:
            yield "event: open\ndata: {}\n\n"

            # Replay missed messages if reconnecting
            if last_id is not None:
                missed = _MASTER_STREAM_HISTORY.replay_from(last_id)
                for msg_id, payload in missed:
                    yield f"id: {msg_id}\ndata: {json.dumps(payload)}\n\n"

            # Send current snapshot
            snapshot = _build_master_snapshot()
            msg_id = _MASTER_STREAM_HISTORY.add({'type': 'snapshot', **snapshot})
            yield f"id: {msg_id}\ndata: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"

            while True:
                try:
                    payload = q.get(timeout=15)
                    # Message ID is already assigned in broadcast function
                    # Just get the current counter for this message
                    msg_id = _MASTER_STREAM_HISTORY.counter
                    yield f"id: {msg_id}\ndata: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    # Send heartbeat to detect disconnected clients
                    yield ": heartbeat\n\n"
        finally:
            _MASTER_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@APP.get("/tasks")
def list_tasks():
    snapshot = _build_tasks_snapshot()
    return jsonify(snapshot)


@APP.get("/orchestrators")
def list_orchestrators():
    return jsonify({"count": len(_build_orchestrator_list()), "orchestrators": _build_orchestrator_list()})


@APP.post("/orchestrators")
def create_orchestrator():
    _ensure_background_threads_started()
    body, err = _require_json_body()
    if err:
        return err
    name = (body.get("name") or "").strip()
    name_err = _validate_name(name, "name")
    if name_err:
        return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
    provider = (body.get("provider") or DEFAULT_PROVIDER).lower()
    provider_err = _validate_provider(provider, allow_default=True)
    if provider_err:
        provider = DEFAULT_PROVIDER
    managed = body.get("managed_sessions")
    if not isinstance(managed, list):
        managed = []
    goal = (body.get("goal") or "").strip()
    if not goal:
        goal = (
            "Act as a project manager across any task type. Always do the next concrete step toward completion: "
            "decide, execute, then report. After every session reply, inject the single most valuable next action "
            "(no questions unless truly blocking). Run tests or a quick manual run when relevant, fix errors until "
            "the objective is complete, and use MCP tools (e.g., Playwright) to validate outputs or UI. "
            "Keep progress moving without waiting for human input."
        )
    enabled = bool(body.get("enabled", True))
    orch_id = uuid.uuid4().hex
    record = {
        "id": orch_id,
        "name": name,
        "provider": provider,
        "managed_sessions": managed,
        "goal": goal,
        "enabled": enabled,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "history": [],
    }
    with _ORCH_LOCK:
        data = _load_orchestrators()
        data[orch_id] = record
        _save_orchestrators(data)
    if enabled and managed:
        for name in managed:
            try:
                _maybe_orchestrator_kickoff(orch_id, record, name)
            except Exception as e:
                logger.error(f"Failed to kickoff orchestrator {orch_id} for session {name}: {e}", exc_info=True)
    return jsonify({"ok": True, "orchestrator": record})


@APP.patch("/orchestrators/<orch_id>")
def update_orchestrator(orch_id):
    body, err = _require_json_body()
    if err:
        return err
    with _ORCH_LOCK:
        data = _load_orchestrators()
        orch = data.get(orch_id)
        if not orch:
            return _error_response("Orchestrator not found", code=ERR_ORCHESTRATOR_NOT_FOUND, status=404)
        if "name" in body:
            new_name = (body.get("name") or "").strip()
            if new_name:
                name_err = _validate_name(new_name, "name")
                if name_err:
                    return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
                orch["name"] = new_name
        if "provider" in body:
            provider = (body.get("provider") or "").strip().lower()
            provider_err = _validate_provider(provider)
            if not provider_err:
                orch["provider"] = provider
            else:
                return _error_response(provider_err, code=ERR_INVALID_PROVIDER, status=400)
        if "managed_sessions" in body and isinstance(body.get("managed_sessions"), list):
            orch["managed_sessions"] = body.get("managed_sessions")
        if "goal" in body:
            orch["goal"] = (body.get("goal") or "").strip()
        if "enabled" in body:
            orch["enabled"] = bool(body.get("enabled"))
        data[orch_id] = orch
        _save_orchestrators(data)
    return jsonify({"ok": True, "orchestrator": orch})


@APP.post("/orchestrators/<orch_id>/start")
def start_orchestrator(orch_id):
    _ensure_background_threads_started()
    with _ORCH_LOCK:
        data = _load_orchestrators()
        orch = data.get(orch_id)
        if not orch:
            return _error_response("Orchestrator not found", code=ERR_ORCHESTRATOR_NOT_FOUND, status=404)
        orch["enabled"] = True
        data[orch_id] = orch
        _save_orchestrators(data)
    managed = orch.get("managed_sessions") or []
    for name in managed:
        try:
            _maybe_orchestrator_kickoff(orch_id, orch, name)
        except Exception as e:
            logger.error(f"Failed to kickoff orchestrator {orch_id} for session {name} on start: {e}", exc_info=True)
    return jsonify({"ok": True})


@APP.post("/orchestrators/<orch_id>/pause")
def pause_orchestrator(orch_id):
    with _ORCH_LOCK:
        data = _load_orchestrators()
        orch = data.get(orch_id)
        if not orch:
            return _error_response("Orchestrator not found", code=ERR_ORCHESTRATOR_NOT_FOUND, status=404)
        orch["enabled"] = False
        data[orch_id] = orch
        _save_orchestrators(data)
    return jsonify({"ok": True})


@APP.delete("/orchestrators/<orch_id>")
def delete_orchestrator(orch_id):
    with _ORCH_LOCK:
        data = _load_orchestrators()
        if orch_id not in data:
            return _error_response("Orchestrator not found", code=ERR_ORCHESTRATOR_NOT_FOUND, status=404)
        data.pop(orch_id, None)
        _save_orchestrators(data)
    return jsonify({"ok": True})


@APP.post("/orchestrators/<orch_id>/respond")
def respond_to_orchestrator(orch_id):
    """User responds to an orchestrator's ask_human question.

    The response is injected to the target session as a user message.
    """
    payload = request.get_json() or {}
    response = (payload.get("response") or "").strip()

    if not response:
        return _error_response("response required", code=ERR_MISSING_REQUIRED_FIELD, status=400)

    with _ORCH_LOCK:
        data = _load_orchestrators()
        orch = data.get(orch_id)
        if not orch:
            return _error_response("Orchestrator not found", code=ERR_ORCHESTRATOR_NOT_FOUND, status=404)

        pending = orch.get("pending_question")
        if not pending:
            return _error_response("no pending question", code=ERR_INVALID_INPUT, status=400)

        target_session = pending.get("target_session")
        question = pending.get("question")

        if not target_session:
            return _error_response("invalid pending question", code=ERR_INVALID_INPUT, status=400)

        # Inject user response to the target session as a user message
        # The session will see this and respond accordingly
        # The orchestrator will see the full conversation history on next decision
        _inject_prompt_to_session(target_session, response)

        # Clear pending question
        orch.pop("pending_question", None)
        data[orch_id] = orch
        _save_orchestrators(data)

    return jsonify({"ok": True})


@APP.get("/tasks/stream")
def stream_tasks():
    # Check for reconnection with Last-Event-ID
    last_event_id = request.headers.get('Last-Event-ID')
    last_id = None
    if last_event_id:
        try:
            last_id = int(last_event_id)
        except (ValueError, TypeError):
            pass

    def generate():
        q = queue.Queue(maxsize=100)
        _TASK_SUBSCRIBERS.add(q)
        try:
            # Replay missed messages if reconnecting
            if last_id is not None:
                missed = _TASK_STREAM_HISTORY.replay_from(last_id)
                for msg_id, payload in missed:
                    yield f"id: {msg_id}\ndata: {json.dumps({'type': 'snapshot', **payload})}\n\n"

            # Send current snapshot
            snapshot = _build_tasks_snapshot()
            msg_id = _TASK_STREAM_HISTORY.add(snapshot)
            yield f"id: {msg_id}\ndata: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"

            while True:
                try:
                    payload = q.get(timeout=15)
                    # Message ID is already assigned in broadcast function
                    msg_id = _TASK_STREAM_HISTORY.counter
                    yield f"id: {msg_id}\ndata: {json.dumps({'type': 'snapshot', **payload})}\n\n"
                except queue.Empty:
                    # Send heartbeat to detect disconnected clients
                    yield ": heartbeat\n\n"
        finally:
            _TASK_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@APP.post("/tasks")
def create_task():
    body, err = _require_json_body()
    if err:
        return err
    name = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    provider = (body.get("provider") or DEFAULT_PROVIDER).lower()
    schedule = body.get("schedule") if isinstance(body.get("schedule"), dict) else {"type": "manual"}
    enabled = bool(body.get("enabled", True))
    workdir = (body.get("workdir") or "").strip()
    name_err = _validate_name(name, "name")
    if name_err:
        return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
    if not prompt:
        return _error_response("prompt is required", code=ERR_INVALID_PROMPT, status=400)
    provider_err = _validate_provider(provider)
    if provider_err:
        return _error_response(provider_err, code=ERR_INVALID_PROVIDER, status=400)
    schedule_err = _validate_schedule(schedule)
    if schedule_err:
        return _error_response(schedule_err, code=ERR_INVALID_SCHEDULE, status=400)
    task = _normalize_task(
        {
            "id": uuid.uuid4().hex,
            "name": name,
            "prompt": prompt,
            "provider": provider,
            "schedule": schedule,
            "enabled": enabled,
            "workdir": workdir,
        }
    )
    if enabled:
        next_dt = _compute_next_run(task)
        task["next_run"] = next_dt.isoformat(timespec="seconds") if next_dt else None
    with _TASK_LOCK:
        tasks = _load_tasks()
        tasks[task["id"]] = task
        _save_tasks(tasks)
    _broadcast_tasks_snapshot()
    return jsonify({"ok": True, "task": task})


@APP.patch("/tasks/<task_id>")
def update_task(task_id):
    body, err = _require_json_body()
    if err:
        return err
    with _TASK_LOCK:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return _error_response("Task not found", code=ERR_TASK_NOT_FOUND, status=404)
        if "name" in body:
            name = (body.get("name") or "").strip()
            name_err = _validate_name(name, "name")
            if name_err:
                return _error_response(name_err, code=ERR_INVALID_INPUT, status=400)
            task["name"] = name
        if "prompt" in body:
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return _error_response("prompt is required", code=ERR_INVALID_PROMPT, status=400)
            task["prompt"] = prompt
        if "provider" in body:
            provider = (body.get("provider") or "").strip().lower()
            provider_err = _validate_provider(provider)
            if provider_err:
                return _error_response(provider_err, code=ERR_INVALID_PROVIDER, status=400)
            task["provider"] = provider
        if "schedule" in body:
            schedule = body.get("schedule") if isinstance(body.get("schedule"), dict) else {"type": "manual"}
            schedule_err = _validate_schedule(schedule)
            if schedule_err:
                return _error_response(schedule_err, code=ERR_INVALID_SCHEDULE, status=400)
            task["schedule"] = schedule
        if "workdir" in body:
            task["workdir"] = (body.get("workdir") or "").strip()
        if "enabled" in body:
            task["enabled"] = bool(body.get("enabled"))
            # Clear running status when disabling task to prevent stuck "running" indicator
            if not task["enabled"] and task.get("last_status") == "running":
                task["last_status"] = "idle"
        if task.get("enabled"):
            next_dt = _compute_next_run(task)
            task["next_run"] = next_dt.isoformat(timespec="seconds") if next_dt else None
        else:
            task["next_run"] = None
        tasks[task_id] = task
        _save_tasks(tasks)
    _broadcast_tasks_snapshot()
    return jsonify({"ok": True, "task": task})


@APP.post("/tasks/<task_id>/run")
def run_task(task_id):
    with _TASK_LOCK:
        tasks = _load_tasks()
        if task_id not in tasks:
            return _error_response("Task not found", code=ERR_TASK_NOT_FOUND, status=404)
    _run_task_async(task_id, force_run=True)
    return jsonify({"ok": True})


@APP.delete("/tasks/<task_id>")
def delete_task(task_id):
    with _TASK_LOCK:
        tasks = _load_tasks()
        removed = tasks.pop(task_id, None)
        _save_tasks(tasks)
    if removed:
        _broadcast_tasks_snapshot()
        return jsonify({"deleted": task_id})
    return _error_response("Task not found", code=ERR_TASK_NOT_FOUND, status=404)


@APP.get("/tasks/<task_id>/stream")
def task_stream(task_id):
    def event_stream():
        q, unsubscribe = _task_stream_subscribe(task_id)
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    payload = q.get(timeout=10)
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                event = payload.get("event", "message")
                data = payload.get("data", {})
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        finally:
            unsubscribe()

    return Response(event_stream(), mimetype="text/event-stream")


@APP.post("/sessions/<name>/provider")
def set_session_provider(name):
    name_err = _validate_name(name, "name")
    if name_err:
        return jsonify({"error": name_err}), 400
    if _get_session_status(name) == "running":
        return jsonify({"error": "session is running"}), 409
    body, err = _require_json_body()
    if err:
        return err
    provider = (body.get("provider") or "").strip().lower()
    provider_err = _validate_provider(provider)
    if provider_err:
        return jsonify({"error": provider_err}), 400
    _set_session_provider(name, provider)
    return jsonify({"ok": True, "provider": provider})


@APP.post("/sessions/<name>/rename")
def rename_session(name):
    name_err = _validate_name(name, "name")
    if name_err:
        return jsonify({"error": name_err}), 400
    if _get_session_status(name) == "running":
        return jsonify({"error": "session is running"}), 409
    body, err = _require_json_body()
    if err:
        return err
    new_name = (body.get("new_name") or "").strip()
    new_err = _validate_name(new_name, "new_name")
    if new_err:
        return jsonify({"error": new_err}), 400
    if new_name == name:
        return jsonify({"ok": True, "name": new_name})
    with _SESSION_LOCK:
        data = _load_sessions()
        if name not in data:
            return jsonify({"error": "not found"}), 404
        if new_name in data:
            return jsonify({"error": "name already exists"}), 409
        record = data.pop(name)
        data[new_name] = record
        _save_sessions(data)
        status = _SESSION_STATUS.pop(name, None)
        if status:
            _SESSION_STATUS[new_name] = status
    _broadcast_sessions_snapshot()
    return jsonify({"ok": True, "name": new_name})


@APP.post("/sessions")
def create_session():
    body, err = _require_json_body()
    if err:
        return err
    name = (body.get("name") or "").strip()
    name_err = _validate_name(name, "name")
    if name_err:
        return jsonify({"error": name_err}), 400
    provider = (body.get("provider") or DEFAULT_PROVIDER).lower()
    provider_err = _validate_provider(provider, allow_default=True)
    if provider_err:
        provider = DEFAULT_PROVIDER
    session_id_override = (body.get("session_id") or "").strip()
    workdir = (body.get("workdir") or "").strip()
    run_init = body.get("run_init", False)
    
    with _SESSION_LOCK:
        data = _load_sessions()
        if name in data:
            return jsonify({"error": "session already exists"}), 409
        if provider == "claude":
            session_id = session_id_override or None
        else:
            session_id = session_id_override or f"{provider}-{uuid.uuid4().hex}"
        record = {
            "session_id": session_id,
            "session_ids": {provider: session_id},
            "provider": provider,
            "last_used": datetime.datetime.now().isoformat(timespec="seconds"),
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        if workdir:
            record["workdir"] = workdir
        data[name] = record
        _save_sessions(data)
    
    # Auto-init if requested and workdir is provided
    if workdir and run_init:
        try:
            logger.info(f"Auto-init for session '{name}' with workdir: {workdir}")
            # Run /init in background to establish context
            def run_init():
                try:
                    cwd = _safe_cwd(workdir)
                    config = _get_provider_config()
                    
                    if provider == "codex":
                        # Codex supports /init as a prompt command
                        result = _run_codex_exec("/init", cwd, extra_args=None, timeout_sec=120, resume_session_id=None, json_events=True)
                        if result and isinstance(result, list):
                            new_session_id = _extract_session_id(result)
                            if new_session_id:
                                with _SESSION_LOCK:
                                    data = _load_sessions()
                                    if name in data and isinstance(data[name], dict):
                                        data[name]["session_id"] = new_session_id
                                        data[name]["session_ids"][provider] = new_session_id
                                        _save_sessions(data)
                                logger.info(f"Auto-init completed for session '{name}', session_id: {new_session_id}")
                    elif provider == "copilot":
                        # Copilot: Request to create COPILOT.md with file permissions
                        init_prompt = f"/init and create a COPILOT.md file in {cwd}"
                        proc, args = _run_copilot_exec(init_prompt, cwd, config, extra_args=["--allow-all-paths"], timeout_sec=120, resume_session_id=None)
                        logger.info(f"Auto-init completed for Copilot session '{name}'")
                    elif provider == "claude":
                        # Claude: Run /init via stdin for multi-line support consistency
                        _run_claude_exec("/init", config=config, cwd=cwd, timeout_sec=240)
                        logger.info(f"Auto-init completed for Claude session '{name}'")
                    elif provider == "gemini":
                        # Gemini: Skip for now - has issues with tool execution
                        logger.info(f"Skipping auto-init for Gemini (not supported)")
                except Exception as e:
                    logger.warning(f"Auto-init failed for session '{name}': {e}")
            
            threading.Thread(target=run_init, daemon=True).start()
        except Exception as e:
            logger.warning(f"Failed to start auto-init thread: {e}")
    
    _broadcast_sessions_snapshot()
    return jsonify({"ok": True, "name": name, "provider": provider})


@APP.delete("/sessions/<name>")
def delete_session(name):
    name_err = _validate_name(name, "name")
    if name_err:
        return jsonify({"error": name_err}), 400
    with _SESSION_LOCK:
        data = _load_sessions()
        removed = data.pop(name, None)
        _save_sessions(data)
        if removed:
            # Get workdir from removed session
            workdir = None
            if isinstance(removed, dict):
                workdir = removed.get("workdir")
            
            history = _load_history(workdir)
            # Remove all session_ids associated with this session from history
            session_ids_to_delete = []
            if isinstance(removed, dict):
                session_ids = removed.get("session_ids") or {}
                session_ids_to_delete = list(session_ids.values())
                if removed.get("session_id"):
                    session_ids_to_delete.append(removed["session_id"])
            elif isinstance(removed, str):
                session_ids_to_delete = [removed]
            changed = False
            for sid in session_ids_to_delete:
                if sid and sid in history:
                    history.pop(sid, None)
                    changed = True
            if changed:
                _save_history(history, workdir)
    if removed:
        _broadcast_sessions_snapshot()
        _SESSION_STATUS.pop(name, None)
        return jsonify({"deleted": name})
    return jsonify({"error": "not found"}), 404


@APP.get("/api/usage")
def get_usage_stats():
    """Get API usage statistics from log.jsonl"""
    time_range = request.args.get("range", "24h")  # 24h, 7d, 30d, all

    # Calculate time threshold
    now = time.time()
    if time_range == "24h":
        threshold = now - (24 * 3600)
    elif time_range == "7d":
        threshold = now - (7 * 24 * 3600)
    elif time_range == "30d":
        threshold = now - (30 * 24 * 3600)
    else:
        threshold = 0  # all time

    try:
        stats = {
            "total_calls": 0,
            "by_provider": {},
            "by_session": {},
            "orchestrator_calls": 0,
            "user_calls": 0,
            "timeline": [],  # hourly breakdown
        }

        log_path = pathlib.Path(LOG_STORE_PATH)
        if not log_path.exists():
            return jsonify(stats)

        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("type") != "job.start":
                        continue

                    ts = entry.get("ts", 0)
                    if ts < threshold:
                        continue

                    provider = entry.get("provider", "unknown")
                    session_name = entry.get("session_name", "unknown")
                    prompt = entry.get("prompt", "")

                    # Count total
                    stats["total_calls"] += 1

                    # Count by provider
                    stats["by_provider"][provider] = stats["by_provider"].get(provider, 0) + 1

                    # Count by session
                    stats["by_session"][session_name] = stats["by_session"].get(session_name, 0) + 1

                    # Detect orchestrator vs user calls
                    is_orchestrator = (
                        "Project goal:" in prompt or
                        "Based on the latest output" in prompt or
                        "Begin the work immediately" in prompt
                    )
                    if is_orchestrator:
                        stats["orchestrator_calls"] += 1
                    else:
                        stats["user_calls"] += 1

                    # Add to timeline (hourly buckets)
                    hour_bucket = int(ts / 3600) * 3600
                    stats["timeline"].append({"ts": hour_bucket, "provider": provider})

                except (json.JSONDecodeError, KeyError):
                    continue

        # Aggregate timeline
        timeline_agg = {}
        for item in stats["timeline"]:
            bucket = item["ts"]
            provider = item["provider"]
            if bucket not in timeline_agg:
                timeline_agg[bucket] = {}
            timeline_agg[bucket][provider] = timeline_agg[bucket].get(provider, 0) + 1

        stats["timeline"] = [
            {"hour": k, **v} for k, v in sorted(timeline_agg.items())
        ]

        # Sort sessions by usage
        stats["by_session"] = dict(sorted(stats["by_session"].items(), key=lambda x: x[1], reverse=True)[:20])

        return jsonify(stats)

    except Exception as exc:
        logger.error(f"Usage stats error: {exc}")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    # Migrate legacy .codex_ files to new names
    _migrate_legacy_files()

    # Ensure Copilot session-state directory exists
    copilot_session_dir = pathlib.Path.home() / ".copilot" / "session-state"
    copilot_session_dir.mkdir(parents=True, exist_ok=True)

    # Startup cleanup: Clear any stuck "running" statuses from previous runs
    with _TASK_LOCK:
        tasks = _load_tasks()
        changed = False
        for task_id, task in tasks.items():
            if task.get("last_status") == "running":
                task["last_status"] = "idle"
                changed = True
        if changed:
            _save_tasks(tasks)

    port = int(os.environ.get("PORT", "5025"))
    _ensure_background_threads_started()
    APP.run(host="0.0.0.0", port=port, debug=False, threaded=True)
