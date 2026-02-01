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
import logging
from collections import deque

from flask import Flask, jsonify, request, Response, render_template

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('context_debug.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)
logger.info("="*60)
logger.info("Flask app starting up")
logger.info("="*60)


_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
APP = Flask(__name__, template_folder=_TEMPLATE_DIR)
APP.config["TEMPLATES_AUTO_RELOAD"] = True
APP.jinja_env.auto_reload = True

# Restrict Codex to a safe working directory by default.
DEFAULT_CODEX_CWD = os.environ.get("CODEX_CWD", os.getcwd())
SESSION_STORE_PATH = os.environ.get("CODEX_SESSION_STORE", os.path.join(DEFAULT_CODEX_CWD, ".codex_sessions.json"))
HISTORY_STORE_PATH = os.environ.get("CODEX_HISTORY_STORE", os.path.join(DEFAULT_CODEX_CWD, ".codex_history.json"))
CLIENT_CONFIG_PATH = os.environ.get("CODEX_CLIENT_CONFIG", os.path.join(DEFAULT_CODEX_CWD, ".client_config.json"))
LOG_STORE_PATH = os.environ.get("CODEX_LOG_STORE", os.path.join(DEFAULT_CODEX_CWD, ".codex_log.jsonl"))
MCP_JSON_PATH = os.environ.get("MCP_JSON_PATH", os.path.join(DEFAULT_CODEX_CWD, ".mcp.json"))
CODEX_CONFIG_PATH = os.environ.get("CODEX_CONFIG_PATH", os.path.join(DEFAULT_CODEX_CWD, ".codex", "config.toml"))
TASK_STORE_PATH = os.environ.get("CODEX_TASK_STORE", os.path.join(DEFAULT_CODEX_CWD, ".codex_tasks.json"))
CONTEXT_DIR = os.path.join(DEFAULT_CODEX_CWD, ".codex_sessions")
DEFAULT_PROVIDER = "codex"
SUPPORTED_PROVIDERS = {"codex", "copilot", "gemini", "claude"}
_SESSION_LOCK = threading.RLock()
_JOB_LOCK = threading.Lock()
_TASK_LOCK = threading.RLock()
_SESSION_STATUS = {}
_JOBS = {}
_SESSION_SUBSCRIBERS = set()
_TASK_SUBSCRIBERS = set()


def _safe_cwd(candidate):
    if candidate:
        return os.path.abspath(candidate)
    config = _get_provider_config()
    default_cwd = (config.get("default_workdir") or "").strip() if isinstance(config, dict) else ""
    return os.path.abspath(default_cwd or DEFAULT_CODEX_CWD)


def _run_codex_exec(
    prompt,
    cwd,
    extra_args=None,
    timeout_sec=300,
    resume_session_id=None,
    resume_last=False,
    json_events=True,
    context_briefing=None,
):
    if not prompt or not isinstance(prompt, str):
        raise ValueError("prompt must be a non-empty string")
    
    # Inject context briefing if provided and not resuming
    if context_briefing and not resume_session_id and not resume_last:
        logger.info(f"[Context] Injecting {len(context_briefing)} chars of context into codex prompt")
        prompt = f"""# Session Context

Previous conversation history from other providers:

{context_briefing}

---

# Current Request

{prompt}"""
    
    codex_path = _resolve_codex_path()
    if not codex_path:
        raise FileNotFoundError("codex CLI not found (set CODEX_PATH or add to PATH)")
    args = [codex_path]
    sandbox_mode = _get_sandbox_mode(_get_provider_config(), "codex")
    if sandbox_mode:
        args.extend(["--sandbox", sandbox_mode])
    args.append("exec")
    # Always skip git repo trust check to match user's preference.
    args.append("--skip-git-repo-check")
    if extra_args:
        args.extend(extra_args)
    if json_events:
        args.append("--json")
    if resume_session_id or resume_last:
        args.append("resume")
        if resume_session_id:
            args.append(resume_session_id)
        else:
            args.append("--last")
    args.append(prompt)
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_sec,
    )
    return proc, args


def _resolve_codex_path():
    return os.environ.get("CODEX_PATH") or shutil.which("codex") or shutil.which("codex.cmd")


def _resolve_copilot_path(config):
    return config.get("copilot_path") or shutil.which("copilot") or shutil.which("copilot.cmd")


def _resolve_gemini_path(config):
    return config.get("gemini_path") or shutil.which("gemini") or shutil.which("gemini.cmd")

def _resolve_claude_path(config):
    return config.get("claude_path") or shutil.which("claude") or shutil.which("claude.cmd")


def _provider_path_status(config):
    return {
        "codex": bool(_resolve_codex_path()),
        "copilot": bool(_resolve_copilot_path(config)),
        "gemini": bool(_resolve_gemini_path(config)),
        "claude": bool(_resolve_claude_path(config)),
    }

def _get_provider_model_info():
    """Get current model info for each provider by reading their config files."""
    models = {}
    config = _load_client_config()
    
    # Codex: read from ~/.codex/config.toml
    try:
        codex_config = pathlib.Path.home() / ".codex" / "config.toml"
        if codex_config.exists():
            import re
            content = codex_config.read_text()
            match = re.search(r'^model\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                models["codex"] = match.group(1)
    except Exception:
        pass
    
    # Copilot: read from client config (user-configurable in this app)
    copilot_model = (config.get("copilot_model") or "").strip()
    models["copilot"] = copilot_model if copilot_model else None
    
    # Gemini: read from ~/.gemini/settings.json
    try:
        gemini_config = pathlib.Path.home() / ".gemini" / "settings.json"
        if gemini_config.exists():
            import json
            data = json.loads(gemini_config.read_text())
            models["gemini"] = data.get("model")
    except Exception:
        pass
    
    # Claude: read from ~/.claude/settings.json
    try:
        claude_config = pathlib.Path.home() / ".claude" / "settings.json"
        if claude_config.exists():
            import json
            data = json.loads(claude_config.read_text())
            models["claude"] = data.get("model")
    except Exception:
        pass
    
    return models

def _get_provider_config():
    return _load_client_config()

def _full_permissions_enabled(config, provider=None):
    if not isinstance(config, dict):
        return True
    if provider:
        key = f"full_permissions_{provider}"
        if key in config:
            return bool(config.get(key))
    return bool(config.get("full_permissions", True))


def _get_sandbox_mode(config, provider):
    if not isinstance(config, dict):
        return ""
    key = f"sandbox_mode_{provider}"
    return (config.get(key) or "").strip()


def _get_mcp_servers(mcp_data):
    if not isinstance(mcp_data, dict):
        return None
    if isinstance(mcp_data.get("mcpServers"), dict):
        return mcp_data.get("mcpServers")
    if isinstance(mcp_data.get("servers"), dict):
        return mcp_data.get("servers")
    return None


def _load_mcp_json(config):
    raw = (config or {}).get("mcp_json") or ""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid MCP JSON: {exc}") from exc


def _write_mcp_json_file(mcp_data):
    path = pathlib.Path(MCP_JSON_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(mcp_data, dict) and "mcpServers" not in mcp_data and "servers" in mcp_data:
        normalized = dict(mcp_data)
        normalized["mcpServers"] = normalized.pop("servers")
        mcp_data = normalized
    path.write_text(json.dumps(mcp_data, indent=2), encoding="utf-8")
    return str(path)


def _toml_escape(value):
    return json.dumps(str(value))


def _write_codex_mcp_config(mcp_data):
    if not isinstance(mcp_data, dict):
        return None
    servers = _get_mcp_servers(mcp_data)
    if not isinstance(servers, dict) or not servers:
        return None
    lines = ["[mcp_servers]"]
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        lines.append(f"[mcp_servers.{name}]")
        if spec.get("url"):
            lines.append(f"url = {_toml_escape(spec.get('url'))}")
        if spec.get("command"):
            lines.append(f"command = {_toml_escape(spec.get('command'))}")
        if isinstance(spec.get("args"), list):
            args = ", ".join(_toml_escape(x) for x in spec.get("args"))
            lines.append(f"args = [{args}]")
        env = spec.get("env")
        if isinstance(env, dict) and env:
            entries = ", ".join(f"{k}={_toml_escape(v)}" for k, v in env.items())
            lines.append(f"env = {{ {entries} }}")
        lines.append("")
    path = pathlib.Path(CODEX_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return str(path)


def _extract_session_id(events):
    for evt in events:
        if not isinstance(evt, dict):
            continue
        for key in ("session_id", "sessionId", "session", "thread_id", "threadId"):
            val = evt.get(key)
            if isinstance(val, str) and val:
                return val
    return None


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


def _normalize_session_record(value):
    if isinstance(value, dict):
        session_id = value.get("session_id")
        session_ids = value.get("session_ids")
        provider = (value.get("provider") or DEFAULT_PROVIDER).lower()
        if provider not in SUPPORTED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        if not isinstance(session_ids, dict):
            session_ids = {}
        if session_id and provider and not session_ids.get(provider):
            session_ids[provider] = session_id
        record = {"session_id": session_id, "session_ids": session_ids, "provider": provider}
        # Preserve workdir if set
        workdir = (value.get("workdir") or "").strip()
        if workdir:
            record["workdir"] = workdir
        return record
    if isinstance(value, str):
        return {"session_id": value, "session_ids": {}, "provider": DEFAULT_PROVIDER}
    return {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}


def _normalize_sessions(data):
    sessions = {}
    if not isinstance(data, dict):
        return sessions
    for name, value in data.items():
        sessions[name] = _normalize_session_record(value)
    return sessions


def _normalize_task(value):
    if not isinstance(value, dict):
        return None
    task_id = value.get("id") or value.get("task_id") or uuid.uuid4().hex
    name = (value.get("name") or "").strip() or f"task-{task_id[:6]}"
    prompt = (value.get("prompt") or "").strip()
    provider = (value.get("provider") or DEFAULT_PROVIDER).lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    schedule = value.get("schedule") if isinstance(value.get("schedule"), dict) else {"type": "manual"}
    enabled = bool(value.get("enabled", True))
    last_run = value.get("last_run")
    next_run = value.get("next_run")
    last_status = value.get("last_status")
    last_output = value.get("last_output")
    last_error = value.get("last_error")
    return {
        "id": task_id,
        "name": name,
        "prompt": prompt,
        "provider": provider,
        "schedule": schedule,
        "enabled": enabled,
        "last_run": last_run,
        "next_run": next_run,
        "last_status": last_status,
        "last_output": last_output,
        "last_error": last_error,
    }


def _load_tasks():
    path = pathlib.Path(TASK_STORE_PATH)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    tasks = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            task = _normalize_task(value)
            if task:
                tasks[task["id"]] = task
    elif isinstance(raw, list):
        for value in raw:
            task = _normalize_task(value)
            if task:
                tasks[task["id"]] = task
    return tasks


def _save_tasks(tasks):
    path = pathlib.Path(TASK_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {task_id: task for task_id, task in (tasks or {}).items()}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    if kind in ("daily", "weekly", "once"):
        time_str = schedule.get("time") or ""
        try:
            hour, minute = [int(x) for x in time_str.split(":", 1)]
        except Exception:
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
    return None


def _broadcast_tasks_snapshot():
    snapshot = _build_tasks_snapshot()
    for q in list(_TASK_SUBSCRIBERS):
        try:
            q.put_nowait(snapshot)
        except queue.Full:
            pass


def _build_tasks_snapshot():
    with _TASK_LOCK:
        tasks = _load_tasks()
    ordered = sorted(tasks.values(), key=lambda t: t.get("name", ""))
    for task in ordered:
        task["schedule_summary"] = _schedule_summary(task)
    return {"count": len(ordered), "tasks": ordered}


def _load_sessions():
    path = pathlib.Path(SESSION_STORE_PATH)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_sessions(data)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sessions(data):
    path = pathlib.Path(SESSION_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_client_config():
    path = pathlib.Path(CLIENT_CONFIG_PATH)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_client_config(data):
    path = pathlib.Path(CLIENT_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
            ).strip()
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
    except Exception:
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
    except Exception:
        pass
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
    except Exception:
        pass


def _get_session_id_for_name(name):
    if not name:
        return None
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        provider = (record.get("provider") or DEFAULT_PROVIDER).lower()
        session_ids = record.get("session_ids") or {}
        if isinstance(session_ids, dict) and session_ids.get(provider):
            return session_ids.get(provider)
        return record.get("session_id")


def _get_session_provider_for_name(name):
    if not name:
        return DEFAULT_PROVIDER
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        provider = (record.get("provider") or DEFAULT_PROVIDER).lower()
        return provider if provider in SUPPORTED_PROVIDERS else DEFAULT_PROVIDER


def _get_session_status(name):
    if not name:
        return "idle"
    with _SESSION_LOCK:
        status = _SESSION_STATUS.get(name)
    return status or "idle"


def _set_session_status(name, status):
    if not name:
        return
    with _SESSION_LOCK:
        prev = _SESSION_STATUS.get(name)
        _SESSION_STATUS[name] = status
    if prev != status:
        _broadcast_sessions_snapshot()


def _set_session_name(name, session_id, provider=None):
    if not name or not session_id:
        return
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}
        record["session_id"] = session_id
        if provider is None:
            provider = (record.get("provider") or DEFAULT_PROVIDER).lower()
        if provider not in SUPPORTED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        session_ids = record.get("session_ids")
        if not isinstance(session_ids, dict):
            session_ids = {}
        session_ids[provider] = session_id
        record["session_ids"] = session_ids
        data[name] = record
        _save_sessions(data)
    _broadcast_sessions_snapshot()


def _set_session_provider(name, provider):
    if not name or not provider:
        return
    provider = provider.lower()
    if provider not in SUPPORTED_PROVIDERS:
        return
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}
        record["provider"] = provider
        record["session_id"] = (record.get("session_ids") or {}).get(provider)
        data[name] = record
        _save_sessions(data)
    _broadcast_sessions_snapshot()


def _ensure_session_id(name, provider):
    if not name:
        return None
    provider = (provider or DEFAULT_PROVIDER).lower()
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {"session_id": None, "session_ids": {}, "provider": provider}
        if record.get("provider") != provider:
            record["provider"] = provider
        session_ids = record.get("session_ids")
        if not isinstance(session_ids, dict):
            session_ids = {}
        if not session_ids.get(provider):
            session_ids[provider] = f"{provider}-{uuid.uuid4().hex}"
        record["session_ids"] = session_ids
        record["session_id"] = session_ids.get(provider)
        data[name] = record
        _save_sessions(data)
        _broadcast_sessions_snapshot()
        return record["session_id"]


def _append_history(session_id, session_name, conversation):
    """Append conversation to history in the appropriate directory.
    
    Args:
        session_id: Session identifier
        session_name: Name of the session (to look up workdir)
        conversation: Conversation data with messages and tool_outputs
    """
    if not session_id or not conversation:
        return
    messages = conversation.get("messages") or []
    tool_outputs = conversation.get("tool_outputs") or []
    if not messages and not tool_outputs:
        return
    
    # Get workdir from session record
    workdir = None
    if session_name:
        with _SESSION_LOCK:
            sessions = _load_sessions()
            record = sessions.get(session_name) or {}
            workdir = record.get("workdir")
    
    with _SESSION_LOCK:
        data = _load_history(workdir)
        entry = data.get(session_id) or {"session_id": session_id, "messages": [], "tool_outputs": []}
        if session_name:
            entry["session_name"] = session_name
        entry["messages"].extend(messages)
        entry["tool_outputs"].extend(tool_outputs)
        data[session_id] = entry
        _save_history(data, workdir)


def _sessions_with_status(sessions):
    status = {}
    for name in sessions.keys():
        status[name] = _get_session_status(name)
    return status


def _build_session_list(sessions):
    items = []
    for name, record in sessions.items():
        items.append(
            {
                "name": name,
                "session_id": record.get("session_id"),
                "provider": record.get("provider") or DEFAULT_PROVIDER,
            }
        )
    return items


def _build_sessions_snapshot():
    with _SESSION_LOCK:
        sessions = _load_sessions()
        status = _sessions_with_status(sessions)
    return {"sessions": sessions, "status": status}


def _broadcast_sessions_snapshot():
    payload = _build_sessions_snapshot()
    dead = []
    for q in list(_SESSION_SUBSCRIBERS):
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass
        except Exception:
            dead.append(q)
    for q in dead:
        _SESSION_SUBSCRIBERS.discard(q)


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


def _run_copilot_exec(prompt, cwd, config, extra_args=None, timeout_sec=300, resume_session_id=None, resume_last=False, context_briefing=None):
    logger.debug(f"[Context] _run_copilot_exec called with context={context_briefing is not None}, resume={resume_session_id}, last={resume_last}")
    # Inject context briefing if provided and not resuming
    if context_briefing and not resume_session_id and not resume_last:
        logger.info(f"[Context] Injecting {len(context_briefing)} chars of context into copilot prompt")
        prompt = f"""# Session Context

Previous conversation history from other providers:

{context_briefing}

---

# Current Request

{prompt}"""
    else:
        logger.debug(f"[Context] Skipping injection: context={context_briefing is not None}, resume={resume_session_id}, last={resume_last}")
    
    copilot_path = _resolve_copilot_path(config)
    if not copilot_path:
        raise FileNotFoundError("copilot CLI not found")
    
    args = [copilot_path]
    
    # Add resume flag if resuming a session (must come before -p)
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    elif resume_last:
        args.append("--continue")
    
    # Add permission flags based on config
    copilot_permissions = (config.get("copilot_permissions") or "").strip()
    if copilot_permissions:
        args.append(f"--{copilot_permissions}")
    
    # Add model flag if configured
    copilot_model = (config.get("copilot_model") or "").strip()
    if copilot_model:
        args.extend(["--model", copilot_model])
    
    # Add prompt flag last
    args.extend(["-p", prompt])
    
    mcp_data = _load_mcp_json(config)
    if mcp_data:
        mcp_path = _write_mcp_json_file(mcp_data)
        args.extend(["--additional-mcp-config", f"@{mcp_path}"])
    if extra_args:
        args.extend(extra_args)
    env = os.environ.copy()
    token = (config.get("copilot_token") or "").strip()
    token_env = (config.get("copilot_token_env") or "GH_TOKEN").strip() or "GH_TOKEN"
    if token:
        env[token_env] = token
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_sec,
        env=env,
    )
    return proc, args


def _run_gemini_exec(prompt, history_messages, config, timeout_sec=300, cwd=None, resume_session_id=None, resume_last=False, context_briefing=None):
    # Inject context briefing if provided and not resuming
    if context_briefing and not resume_session_id and not resume_last:
        logger.info(f"[Context] Injecting {len(context_briefing)} chars of context into gemini prompt")
        prompt = f"""# Session Context

Previous conversation history from other providers:

{context_briefing}

---

# Current Request

{prompt}"""
    
    gemini_path = _resolve_gemini_path(config)
    if not gemini_path:
        raise FileNotFoundError("gemini CLI not found")
    
    args = [gemini_path]
    
    # Add resume flag if resuming a session (must come before -p)
    # Gemini manages its own session IDs, so we always use 'latest' to continue
    if resume_session_id or resume_last:
        args.extend(["--resume", "latest"])
    
    # Add prompt flag last
    args.extend(["-p", prompt])
    
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gemini CLI failed").strip())
    return (proc.stdout or "").strip()


def _run_claude_exec(prompt, config, timeout_sec=300, cwd=None, resume_session_id=None, resume_last=False, context_briefing=None):
    # Inject context briefing if provided and not resuming
    if context_briefing and not resume_session_id and not resume_last:
        logger.info(f"[Context] Injecting {len(context_briefing)} chars of context into claude prompt")
        prompt = f"""# Session Context

Previous conversation history from other providers:

{context_briefing}

---

# Current Request

{prompt}"""
    
    claude_path = _resolve_claude_path(config)
    if not claude_path:
        raise FileNotFoundError("claude CLI not found")
    
    args = [claude_path]
    
    # Add resume flag if resuming a session (must come before -p)
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    elif resume_last:
        args.append("--continue")
    
    # Add prompt flag last
    args.extend(["-p", prompt])
    
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "claude CLI failed").strip())
    return (proc.stdout or "").strip()


@APP.get("/health")
def health():
    return jsonify({"ok": True})


@APP.get("/diag")
def diag():
    ip_hint = ""
    try:
        ip_hint = subprocess.check_output("ipconfig", text=True, encoding="utf-8", errors="ignore")
    except Exception:
        ip_hint = ""
    template_path = APP.jinja_loader.searchpath if APP.jinja_loader else []
    template_has_task_menu = False
    try:
        tmpl_path = os.path.join(APP.root_path, "templates", "chat.html")
        if os.path.exists(tmpl_path):
            template_has_task_menu = "task-menu" in pathlib.Path(tmpl_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
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
    except Exception:
        ipconfig = "Unable to read ipconfig output."
    port = int(os.environ.get("PORT", "6000"))
    return render_template("diag.html", ipconfig=ipconfig, port=port)


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
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        selected_provider=DEFAULT_PROVIDER,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
    )


@APP.get("/chat")
def chat_home():
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        selected_provider=DEFAULT_PROVIDER,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
    )


@APP.get("/chat/<name>")
def chat_named(name):
    with _SESSION_LOCK:
        sessions = _load_sessions()
    config = _load_client_config()
    default_workdir = (config.get("default_workdir") or "").strip()
    history = _get_history_for_name(name)
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    selected_provider = _get_session_provider_for_name(name)
    provider_models = _get_provider_model_info()
    # Get session-specific workdir if set
    session_record = sessions.get(name) or {}
    session_workdir = (session_record.get("workdir") or "").strip() if isinstance(session_record, dict) else ""
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        selected=name,
        selected_provider=selected_provider,
        default_provider=DEFAULT_PROVIDER,
        history_messages=history["messages"],
        history_tools=history["tool_outputs"],
        default_workdir=default_workdir,
        session_workdir=session_workdir,
        provider_models=provider_models,
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
    
    config = _load_client_config()
    default_workdir = (config.get("default_workdir") or "").strip()
    session_status = _sessions_with_status(sessions)
    session_list = _build_session_list(sessions)
    provider_models = _get_provider_model_info()
    
    return render_template(
        "chat.html",
        sessions=sessions,
        session_list=session_list,
        session_status=session_status,
        default_provider=DEFAULT_PROVIDER,
        history_messages=[],
        history_tools=[],
        default_workdir=default_workdir,
        provider_models=provider_models,
        selected_task=task,
        view_mode="task",
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
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt")
    extra_args = body.get("extra_args") or []
    timeout_sec = body.get("timeout_sec", 300)
    resume_session_id = body.get("session_id")
    session_name = body.get("session_name")
    requested_provider = body.get("provider")
    logger.debug(f"[Context] /exec: session={session_name}, provider={requested_provider}")
    resume_last = bool(body.get("resume_last", False))
    json_events = bool(body.get("json_events", True))
    try:
        cwd = _safe_cwd(body.get("cwd"))
        if not isinstance(extra_args, list) or not all(isinstance(x, str) for x in extra_args):
            return jsonify({"error": "extra_args must be a list of strings"}), 400
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
            text = (proc.stdout or "").strip()
            events = _build_synthetic_events(text)
            session_id = resume_session_id or _ensure_session_id(session_name, provider) if session_name else None
            result = {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "cwd": cwd,
                "cmd": cmd,
            }
        elif provider == "gemini":
            history_messages = _get_history_for_name(session_name).get("messages") if session_name else []
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
            text = _run_claude_exec(prompt, config=config, timeout_sec=timeout_sec, cwd=cwd, resume_session_id=resume_session_id, resume_last=resume_last, context_briefing=context_briefing)
            claude_path = _resolve_claude_path(config) or "claude"
            events = _build_synthetic_events(text)
            session_id = resume_session_id or _ensure_session_id(session_name, provider) if session_name else None
            result = {
                "returncode": 0,
                "stdout": text,
                "stderr": "",
                "cwd": cwd,
                "cmd": [claude_path, "-p", prompt],
            }
        else:
            return jsonify({"error": "unknown provider"}), 400
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
        return jsonify({"error": "codex exec timed out"}), 504
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except FileNotFoundError:
        return jsonify({"error": "CLI not found in PATH"}), 500
    finally:
        if session_name:
            _set_session_status(session_name, "idle")


def _enqueue_output(pipe, q, label):
    for line in iter(pipe.readline, ""):
        q.put((label, line))
    pipe.close()


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
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def add_subscriber_with_snapshot(self, q):
        with self.lock:
            self.subscribers.add(q)
            return list(self.buffer)


def _build_codex_args(codex_path, extra_args, json_events, resume_session_id, resume_last, prompt, context_briefing=None):
    # Inject context briefing if provided and not resuming
    if context_briefing and not resume_session_id and not resume_last:
        logger.info(f"[Context] Injecting {len(context_briefing)} chars of context into codex stream")
        prompt = f"""# Session Context

Previous conversation history from other providers:

{context_briefing}

---

# Current Request

{prompt}"""
    
    args = [codex_path]
    sandbox_mode = _get_sandbox_mode(_get_provider_config(), "codex")
    if sandbox_mode:
        args.extend(["--sandbox", sandbox_mode])
    args.append("exec")
    args.append("--skip-git-repo-check")
    if extra_args:
        args.extend(extra_args)
    if json_events:
        args.append("--json")
    if resume_session_id or resume_last:
        args.append("resume")
        if resume_session_id:
            args.append(resume_session_id)
        else:
            args.append("--last")
    args.append(prompt)
    return args


def _broadcast_agent_message(job, text):
    if not text:
        return
    evt = {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    job.broadcast(f"data: stdout:{json.dumps(evt)}\n\n")


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
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)


def _start_codex_job(job):
    def runner():
        codex_path = _resolve_codex_path()
        if not codex_path:
            _broadcast_error(job, "codex CLI not found in PATH")
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        args = _build_codex_args(
            codex_path,
            job.extra_args,
            job.json_events,
            job.resume_session_id,
            job.resume_last,
            job.prompt,
            job.context_briefing,
        )
        try:
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError:
            _broadcast_error(job, "codex CLI not found in PATH")
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
                                job.session_id = sess
                                if job.session_name:
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
                conversation["messages"].append({"role": "assistant", "text": "\n".join(assistant_chunks).strip()})
            _append_history(job.session_id, job.session_name, conversation)
        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
        job.done.set()
        _set_session_status(job.session_name, "idle")
        with _JOB_LOCK:
            _JOBS.pop(job.key, None)

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
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        
        args = [copilot_path]
        
        # Add resume flag if resuming a session (must come before -p)
        if job.resume_session_id:
            args.extend(["--resume", job.resume_session_id])
        elif job.resume_last:
            args.append("--continue")
        
        # Add permission flags based on config
        copilot_permissions = (config.get("copilot_permissions") or "").strip()
        if copilot_permissions:
            args.append(f"--{copilot_permissions}")
        
        # Add model flag if configured
        copilot_model = (config.get("copilot_model") or "").strip()
        if copilot_model:
            args.extend(["--model", copilot_model])
        
        # Add prompt flag last
        args.extend(["-p", prompt])
        
        mcp_data = _load_mcp_json(config)
        if mcp_data:
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
        if session_id:
            job.session_id = session_id
            job.broadcast(f"event: session_id\ndata: {session_id}\n\n")
        try:
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except FileNotFoundError:
            _broadcast_error(job, "copilot CLI not found in PATH")
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
                    _broadcast_error(job, "copilot exec timed out")
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
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                conversation["messages"].append({"role": "assistant", "text": "\n".join(assistant_chunks).strip()})
            _append_history(job.session_id, job.session_name, conversation)
        job.broadcast(f"event: done\ndata: returncode={rc}\n\n")
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
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        session_id = job.session_id or (job.session_name and _ensure_session_id(job.session_name, job.provider))
        if session_id:
            job.session_id = session_id
            job.broadcast(f"event: session_id\ndata: {session_id}\n\n")
        
        args = [gemini_path]
        
        # Add resume flag if resuming a session (must come before -p)
        # Gemini manages its own session IDs, so we always use 'latest' to continue
        if job.resume_session_id or job.resume_last:
            args.extend(["--resume", "latest"])
        
        # Add prompt flag last
        args.extend(["-p", prompt])
        try:
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError:
            _broadcast_error(job, "gemini CLI not found in PATH")
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

        proc.wait()

        job.returncode = 0
        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": 0,
            }
        )
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                conversation["messages"].append({"role": "assistant", "text": "\n".join(assistant_chunks).strip()})
            _append_history(job.session_id, job.session_name, conversation)
        job.broadcast("event: done\ndata: returncode=0\n\n")
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
        claude_path = _resolve_claude_path(config)
        if not claude_path:
            _broadcast_error(job, "claude CLI not found in PATH")
            job.done.set()
            _set_session_status(job.session_name, "idle")
            with _JOB_LOCK:
                _JOBS.pop(job.key, None)
            return
        session_id = job.session_id or (job.session_name and _ensure_session_id(job.session_name, job.provider))
        if session_id:
            job.session_id = session_id
            job.broadcast(f"event: session_id\ndata: {session_id}\n\n")
        
        args = [claude_path]
        
        # Add resume flag if resuming a session (must come before -p)
        if job.resume_session_id:
            args.extend(["--resume", job.resume_session_id])
        elif job.resume_last:
            args.append("--continue")
        
        # Add prompt flag last
        args.extend(["-p", prompt])
        try:
            proc = subprocess.Popen(
                args,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError:
            _broadcast_error(job, "claude CLI not found in PATH")
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
                    _broadcast_error(job, "claude exec timed out")
                    break

        proc.wait()

        job.returncode = 0
        _log_event(
            {
                "type": "job.done",
                "provider": job.provider,
                "session_name": job.session_name,
                "session_id": job.session_id,
                "prompt": job.prompt,
                "returncode": 0,
            }
        )
        if job.session_id:
            conversation = {"messages": [], "tool_outputs": []}
            if job.prompt:
                conversation["messages"].append({"role": "user", "text": job.prompt})
            if assistant_chunks:
                conversation["messages"].append({"role": "assistant", "text": "\n".join(assistant_chunks).strip()})
            _append_history(job.session_id, job.session_name, conversation)
        job.broadcast("event: done\ndata: returncode=0\n\n")
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
    cwd = _safe_cwd(None)
    
    # For tasks, we need to ensure non-interactive execution
    if provider == "codex":
        # Override sandbox mode to danger-full-access for tasks
        original_sandbox = config.get("sandbox_mode_codex")
        config["sandbox_mode_codex"] = "danger-full-access"
        try:
            proc, cmd = _run_codex_exec(prompt, cwd, json_events=True)
        finally:
            # Restore original sandbox mode
            if original_sandbox is None:
                config.pop("sandbox_mode_codex", None)
            else:
                config["sandbox_mode_codex"] = original_sandbox
                
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "codex failed").strip())
        # Parse JSON events to get the output
        output_text = (proc.stdout or "").strip()
        try:
            # Extract assistant messages from JSON events
            lines = output_text.split('\n')
            assistant_text = []
            for line in lines:
                if line.startswith('{"type":'):
                    import json
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'item.completed':
                            item = event.get('item', {})
                            if item.get('type') == 'message' and item.get('role') == 'assistant':
                                for content in item.get('content', []):
                                    if content.get('type') == 'text':
                                        assistant_text.append(content.get('text', ''))
                    except:
                        pass
            output_text = '\n'.join(assistant_text) if assistant_text else output_text
        except:
            pass
        return {"output": output_text, "cmd": cmd}
    if provider == "copilot":
        proc, cmd = _run_copilot_exec(prompt, cwd, config=config)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "copilot failed").strip())
        return {"output": (proc.stdout or "").strip(), "cmd": cmd}
    if provider == "gemini":
        text = _run_gemini_exec(prompt, [], config=config, cwd=cwd)
        return {"output": text, "cmd": [_resolve_gemini_path(config) or "gemini", "-p", prompt]}
    if provider == "claude":
        text = _run_claude_exec(prompt, config=config, cwd=cwd)
        return {"output": text, "cmd": [_resolve_claude_path(config) or "claude", "-p", prompt]}
    raise RuntimeError("unknown provider")


def _mark_task_run(task_id, status, output=None, error=None):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _TASK_LOCK:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return
        task["last_run"] = now
        task["last_status"] = status
        if output is not None:
            task["last_output"] = output
        if error is not None:
            task["last_error"] = error
        elif status == "ok":
            # Clear error on successful run
            task["last_error"] = None
        task["next_run"] = None
        if task.get("enabled"):
            next_dt = _compute_next_run(task)
            task["next_run"] = next_dt.isoformat(timespec="seconds") if next_dt else None
        tasks[task_id] = task
        _save_tasks(tasks)
    _broadcast_tasks_snapshot()


def _run_task_async(task_id):
    def runner():
        try:
            with _TASK_LOCK:
                tasks = _load_tasks()
                task = tasks.get(task_id)
                if task:
                    task["last_status"] = "running"
                    _save_tasks(tasks)
            _broadcast_tasks_snapshot()
            
            if not task:
                return
            result = _run_task_exec(task)
            _mark_task_run(task_id, "ok", output=result.get("output") or "")
        except Exception as exc:
            _mark_task_run(task_id, "error", error=str(exc))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


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
        time.sleep(30)


@APP.post("/stream")
def stream_codex():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt")
    extra_args = body.get("extra_args") or []
    timeout_sec = body.get("timeout_sec", 300)
    resume_session_id = body.get("session_id")
    session_name = body.get("session_name")
    requested_provider = body.get("provider")
    resume_last = bool(body.get("resume_last", False))
    json_events = bool(body.get("json_events", True))
    attach = bool(body.get("attach", False))
    try:
        cwd = _safe_cwd(body.get("cwd"))
        if not isinstance(extra_args, list) or not all(isinstance(x, str) for x in extra_args):
            return jsonify({"error": "extra_args must be a list of strings"}), 400
        
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
            logger.debug(f"[Context] Stream - Before resolve: provider={current_provider_before}, session_id={current_session_id_before}")
        
        if not resume_session_id and session_name:
            resume_session_id = _get_session_id_for_name(session_name)
        provider = _resolve_provider(session_name, requested_provider)
        logger.debug(f"[Context] Stream - After resolve: provider={provider}, requested={requested_provider}")
        
        # Check if we're switching providers and need to generate context
        switching_providers = False
        context_summary = ""
        if session_name and current_provider_before and provider != current_provider_before:
            logger.info(f"[Context] Stream - Provider changed: {current_provider_before} -> {provider}")
            # Get session_ids for the NEW provider
            with _SESSION_LOCK:
                data = _load_sessions()
                record = data.get(session_name) or {}
                session_ids = record.get("session_ids") or {}
                new_provider_session_id = session_ids.get(provider)
            logger.debug(f"[Context] Stream - New provider session_id: {new_provider_session_id}")
            
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
                logger.debug(f"[Context] Stream - Not generating: new_session_id={new_provider_session_id}, old_session_id={current_session_id_before}")
        
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

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

    job_key = f"{provider}:{session_name or resume_session_id or f'anon-{uuid.uuid4().hex}'}"
    with _JOB_LOCK:
        existing = _JOBS.get(job_key)
        if existing and not existing.done.is_set():
            if attach or not prompt:
                job = existing
            else:
                return jsonify({"error": "session is already running"}), 409
        else:
            if not prompt or not isinstance(prompt, str):
                return jsonify({"error": "prompt must be a non-empty string"}), 400
            job = _Job(
                job_key,
                session_name,
                prompt,
                cwd,
                extra_args,
                timeout_sec,
                resume_session_id,
                resume_last,
                json_events,
                provider,
                context_briefing=context_briefing,
            )
            _JOBS[job_key] = job
            if session_name:
                _set_session_status(session_name, "running")
            _start_job(job)

    def generate():
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
        finally:
            job.remove_subscriber(subscriber)

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
                payload = q.get()
                yield f"data: {json.dumps({'type': 'snapshot', **payload})}\n\n"
        finally:
            _SESSION_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@APP.get("/tasks")
def list_tasks():
    snapshot = _build_tasks_snapshot()
    return jsonify(snapshot)


@APP.get("/tasks/stream")
def stream_tasks():
    def generate():
        q = queue.Queue(maxsize=100)
        _TASK_SUBSCRIBERS.add(q)
        try:
            snapshot = _build_tasks_snapshot()
            yield f"data: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"
            while True:
                payload = q.get()
                yield f"data: {json.dumps({'type': 'snapshot', **payload})}\n\n"
        finally:
            _TASK_SUBSCRIBERS.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@APP.post("/tasks")
def create_task():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    provider = (body.get("provider") or DEFAULT_PROVIDER).lower()
    schedule = body.get("schedule") if isinstance(body.get("schedule"), dict) else {"type": "manual"}
    enabled = bool(body.get("enabled", True))
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    if provider not in SUPPORTED_PROVIDERS:
        return jsonify({"error": "unknown provider"}), 400
    task = _normalize_task(
        {
            "id": uuid.uuid4().hex,
            "name": name,
            "prompt": prompt,
            "provider": provider,
            "schedule": schedule,
            "enabled": enabled,
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
    body = request.get_json(silent=True) or {}
    with _TASK_LOCK:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return jsonify({"error": "not found"}), 404
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name is required"}), 400
            task["name"] = name
        if "prompt" in body:
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return jsonify({"error": "prompt is required"}), 400
            task["prompt"] = prompt
        if "provider" in body:
            provider = (body.get("provider") or "").strip().lower()
            if provider not in SUPPORTED_PROVIDERS:
                return jsonify({"error": "unknown provider"}), 400
            task["provider"] = provider
        if "schedule" in body:
            schedule = body.get("schedule") if isinstance(body.get("schedule"), dict) else {"type": "manual"}
            task["schedule"] = schedule
        if "enabled" in body:
            task["enabled"] = bool(body.get("enabled"))
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
            return jsonify({"error": "not found"}), 404
    _run_task_async(task_id)
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
    return jsonify({"error": "not found"}), 404


@APP.post("/sessions/<name>/provider")
def set_session_provider(name):
    if _get_session_status(name) == "running":
        return jsonify({"error": "session is running"}), 409
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        return jsonify({"error": "unknown provider"}), 400
    _set_session_provider(name, provider)
    return jsonify({"ok": True, "provider": provider})


@APP.post("/sessions/<name>/rename")
def rename_session(name):
    if _get_session_status(name) == "running":
        return jsonify({"error": "session is running"}), 409
    body = request.get_json(silent=True) or {}
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
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
    body = request.get_json() or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    provider = (body.get("provider") or DEFAULT_PROVIDER).lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    workdir = (body.get("workdir") or "").strip()
    with _SESSION_LOCK:
        data = _load_sessions()
        if name in data:
            return jsonify({"error": "session already exists"}), 409
        session_id = f"{provider}-{uuid.uuid4().hex}"
        record = {
            "session_id": session_id,
            "session_ids": {provider: session_id},
            "provider": provider,
        }
        if workdir:
            record["workdir"] = workdir
        data[name] = record
        _save_sessions(data)
    _broadcast_sessions_snapshot()
    return jsonify({"ok": True, "name": name, "provider": provider})


@APP.delete("/sessions/<name>")
def delete_session(name):
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5025"))
    threading.Thread(target=_task_scheduler_loop, daemon=True).start()
    APP.run(host="0.0.0.0", port=port, debug=False, threaded=True)
