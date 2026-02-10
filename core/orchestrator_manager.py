"""Orchestrator storage and prompt helpers."""
import json
import pathlib
import uuid

from utils.config import ORCH_STORE_PATH, DEFAULT_PROVIDER, SUPPORTED_PROVIDERS
from core.state import _ORCH_LOCK


def _normalize_orchestrator(value):
    if not isinstance(value, dict):
        return None
    orch_id = value.get("id") or uuid.uuid4().hex
    name = (value.get("name") or "").strip() or f"orch-{orch_id[:6]}"
    provider = (value.get("provider") or DEFAULT_PROVIDER).lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    managed = value.get("managed_sessions")
    if not isinstance(managed, list):
        managed = []
    goal = (value.get("goal") or "").strip()
    enabled = bool(value.get("enabled", False))
    created_at = value.get("created_at")
    history = value.get("history")
    if not isinstance(history, list):
        history = []
    last_action = value.get("last_action")
    last_decision_at = value.get("last_decision_at")
    last_question = value.get("last_question")
    pending_question = value.get("pending_question")
    if not isinstance(pending_question, dict):
        pending_question = None
    return {
        "id": orch_id,
        "name": name,
        "provider": provider,
        "managed_sessions": managed,
        "goal": goal,
        "enabled": enabled,
        "created_at": created_at,
        "history": history,
        "last_action": last_action,
        "last_decision_at": last_decision_at,
        "last_question": last_question,
        "pending_question": pending_question,
    }


def _load_orchestrators():
    path = pathlib.Path(ORCH_STORE_PATH)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    items = {}
    if isinstance(raw, dict):
        for _, value in raw.items():
            orch = _normalize_orchestrator(value)
            if orch:
                items[orch["id"]] = orch
    elif isinstance(raw, list):
        for value in raw:
            orch = _normalize_orchestrator(value)
            if orch:
                items[orch["id"]] = orch
    return items


def _save_orchestrators(data):
    path = pathlib.Path(ORCH_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {orch_id: orch for orch_id, orch in (data or {}).items()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_orchestrator_list():
    with _ORCH_LOCK:
        items = list(_load_orchestrators().values())
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items


def _append_orchestrator_history(orch_id, orch, entry):
    if not orch_id or not entry:
        return
    with _ORCH_LOCK:
        data = _load_orchestrators()
        current = data.get(orch_id) or orch or {}
        history = current.get("history")
        if not isinstance(history, list):
            history = []
        history.append(entry)
        if len(history) > 200:
            history = history[-200:]
        current["history"] = history
        data[orch_id] = current
        _save_orchestrators(data)


def _build_orchestrator_history_text(history):
    if not history:
        return ""
    lines = []
    for item in history:
        if not isinstance(item, dict):
            continue
        ts = item.get("at") or ""
        action = item.get("action") or ""
        target = item.get("target_session") or ""
        prompt = item.get("prompt") or ""
        question = item.get("question") or ""
        raw = item.get("raw") or ""
        header_parts = []
        if ts:
            header_parts.append(f"[{ts}]")
        if action:
            header_parts.append(action)
        if target:
            header_parts.append(f"target={target}")
        header = " ".join(header_parts).strip()
        body = prompt or question or raw or ""
        if header and body:
            lines.append(f"{header}\n{body}".rstrip())
        elif header:
            lines.append(header)
        elif body:
            lines.append(body)
    return "\n\n".join(lines).strip()


def _infer_worker_role(goal):
    text = (goal or "").lower()
    if any(k in text for k in ["test", "qa", "verify", "validation"]):
        return "tester"
    if any(k in text for k in ["research", "investigate", "find", "analyze", "compare"]):
        return "researcher"
    if any(k in text for k in ["design", "ui", "ux", "layout", "style"]):
        return "designer"
    if any(k in text for k in ["write", "draft", "document", "doc", "spec"]):
        return "writer"
    return "developer"


def _build_worker_kickoff_prompt(goal, role, template=None, workdir=None):
    if template:
        return template.format(goal=goal, role=role, workdir=workdir or "")
    return (
        f"Project goal:\n{goal}\n"
        f"Session working directory:\n{workdir or ''}\n\n"
        f"You are the {role} working for a manager. Begin implementation immediately. "
        "Do not act as the manager; focus on execution and report progress with concrete results."
    )


def _extract_json_action(text):
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    for end in range(len(text), start, -1):
        chunk = text[start:end]
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None
