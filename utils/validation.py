"""Request validation helpers."""
from flask import request, jsonify
from utils.config import SUPPORTED_PROVIDERS


def _validate_name(value, label="name", max_len=120):
    if not isinstance(value, str):
        return f"{label} must be a string"
    name = value.strip()
    if not name:
        return f"{label} is required"
    if len(name) > max_len:
        return f"{label} must be {max_len} chars or fewer"
    if any(ch in name for ch in ["/", "\\", "\0"]):
        return f"{label} contains invalid characters"
    if name in {".", ".."}:
        return f"{label} is invalid"
    return None


def _validate_provider(value, allow_default=False):
    if not value and allow_default:
        return None
    if not isinstance(value, str):
        return "provider must be a string"
    if value.lower() not in SUPPORTED_PROVIDERS:
        return "unknown provider"
    return None


def _require_json_body(allow_empty=False):
    body = request.get_json(silent=True)
    if body is None:
        if allow_empty:
            return {}, None
        return None, (jsonify({"error": "invalid or missing JSON body"}), 400)
    if not isinstance(body, dict):
        return None, (jsonify({"error": "JSON body must be an object"}), 400)
    return body, None


def _validate_schedule(schedule):
    if schedule is None:
        return None
    if not isinstance(schedule, dict):
        return "schedule must be an object"
    sched_type = (schedule.get("type") or "manual").strip().lower()
    if sched_type not in {"manual", "once", "interval", "daily", "weekly", "monthly"}:
        return "schedule.type is invalid"
    return None
