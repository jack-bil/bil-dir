"""Session storage and status management."""
import json
import pathlib
import datetime

from utils.config import SESSION_STORE_PATH, DEFAULT_PROVIDER, SUPPORTED_PROVIDERS
from core.state import _SESSION_LOCK, _SESSION_STATUS, _SESSION_SUBSCRIBERS, _JOB_LOCK, _JOBS


def _normalize_session_record(value):
    if isinstance(value, dict):
        session_id = value.get("session_id")
        session_ids = value.get("session_ids")
        last_used = value.get("last_used")
        created_at = value.get("created_at")
        provider = (value.get("provider") or DEFAULT_PROVIDER).lower()
        if provider not in SUPPORTED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        if not isinstance(session_ids, dict):
            session_ids = {}
        if session_id and provider and not session_ids.get(provider):
            session_ids[provider] = session_id
        record = {
            "session_id": session_id,
            "session_ids": session_ids,
            "provider": provider,
            "last_used": last_used,
            "created_at": created_at,
        }
        workdir = (value.get("workdir") or "").strip()
        if workdir:
            record["workdir"] = workdir
        return record
    if isinstance(value, str):
        return {
            "session_id": value,
            "session_ids": {DEFAULT_PROVIDER: value},
            "provider": DEFAULT_PROVIDER,
        }
    return {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}


def _normalize_sessions(data):
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for name, value in data.items():
        if not isinstance(name, str):
            continue
        normalized[name] = _normalize_session_record(value)
    return normalized


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


def _get_session_id_for_name(name):
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        provider = record.get("provider") or DEFAULT_PROVIDER
        if provider and record.get("session_ids"):
            return record.get("session_ids", {}).get(provider)
        return record.get("session_id")


def _get_session_provider_for_name(name):
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        return (record.get("provider") or DEFAULT_PROVIDER).lower()


def _get_session_workdir(name):
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {}
        return record.get("workdir")


def _get_session_status(name):
    return _SESSION_STATUS.get(name, "idle")


def _session_has_active_job(name):
    if not name:
        return False
    with _JOB_LOCK:
        for job in _JOBS.values():
            try:
                if job.session_name == name and not job.done.is_set():
                    return True
            except Exception:
                continue
    return False


def _set_session_status(name, status):
    if not name:
        return
    _SESSION_STATUS[name] = status
    _broadcast_sessions_snapshot()


def _set_session_name(name, session_id, provider=None):
    if not name or not session_id:
        return
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}
        record["last_used"] = datetime.datetime.now().isoformat(timespec="seconds")
        if provider is None:
            provider = (record.get("provider") or DEFAULT_PROVIDER).lower()
        provider = provider.lower()
        if provider not in SUPPORTED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        session_ids = record.get("session_ids")
        if not isinstance(session_ids, dict):
            session_ids = {}
        session_ids[provider] = session_id
        record["session_ids"] = session_ids
        record["session_id"] = session_ids.get(provider)
        record["provider"] = provider
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
            import uuid
            if provider == "copilot":
                session_ids[provider] = None
            elif provider == "claude":
                # Do not create synthetic Claude IDs; wait for real UUID from CLI.
                record["session_ids"] = session_ids
                record["session_id"] = session_ids.get(provider)
                data[name] = record
                _save_sessions(data)
                _broadcast_sessions_snapshot()
                return None
            else:
                session_ids[provider] = f"{provider}-{uuid.uuid4().hex}"
        record["session_ids"] = session_ids
        record["session_id"] = session_ids.get(provider)
        data[name] = record
        _save_sessions(data)
        _broadcast_sessions_snapshot()
        return record["session_id"]


def _sessions_with_status(sessions):
    status = {}
    for name in sessions.keys():
        current = _get_session_status(name)
        if current == "running" and not _session_has_active_job(name):
            _SESSION_STATUS[name] = "idle"
            current = "idle"
        status[name] = current
    return status


def _build_session_list(sessions):
    items = []
    for name, record in sessions.items():
        items.append(
            {
                "name": name,
                "session_id": record.get("session_id"),
                "provider": record.get("provider") or DEFAULT_PROVIDER,
                "last_used": record.get("last_used"),
                "created_at": record.get("created_at"),
            }
        )
    items.sort(key=lambda item: item.get("created_at") or item.get("last_used") or "", reverse=True)
    return items


def _build_sessions_snapshot():
    with _SESSION_LOCK:
        sessions = _load_sessions()
        status = _sessions_with_status(sessions)
    return {"sessions": sessions, "status": status}


def _touch_session(name, when=None):
    if not name:
        return
    now = when or datetime.datetime.now().isoformat(timespec="seconds")
    with _SESSION_LOCK:
        data = _load_sessions()
        record = data.get(name) or {"session_id": None, "session_ids": {}, "provider": DEFAULT_PROVIDER}
        record["last_used"] = now
        if not record.get("created_at"):
            record["created_at"] = now
        data[name] = record
        _save_sessions(data)


def _broadcast_sessions_snapshot():
    snapshot = _build_sessions_snapshot()
    for q in list(_SESSION_SUBSCRIBERS):
        try:
            q.put_nowait(snapshot)
        except Exception:
            pass
