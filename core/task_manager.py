"""Task storage helpers."""
import json
import pathlib
import uuid

from utils.config import TASK_STORE_PATH, DEFAULT_PROVIDER, SUPPORTED_PROVIDERS


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
    workdir = (value.get("workdir") or "").strip()
    enabled = bool(value.get("enabled", True))
    last_run = value.get("last_run")
    next_run = value.get("next_run")
    last_status = value.get("last_status")
    last_output = value.get("last_output")
    last_output_raw = value.get("last_output_raw")
    last_error = value.get("last_error")
    run_history = value.get("run_history")
    last_runtime_sec = value.get("last_runtime_sec")
    if not isinstance(run_history, list):
        run_history = []
    return {
        "id": task_id,
        "name": name,
        "prompt": prompt,
        "provider": provider,
        "schedule": schedule,
        "workdir": workdir,
        "enabled": enabled,
        "last_run": last_run,
        "next_run": next_run,
        "last_status": last_status,
        "last_output": last_output,
        "last_output_raw": last_output_raw,
        "last_error": last_error,
        "last_runtime_sec": last_runtime_sec,
        "run_history": run_history,
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
        for _, value in raw.items():
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


def _ensure_task_history(task):
    if not task or task.get("run_history"):
        return task
    has_any = task.get("last_output") or task.get("last_output_raw") or task.get("last_error")
    if not has_any:
        return task
    run = {
        "finished_at": task.get("last_run"),
        "started_at": task.get("last_run"),
        "runtime_sec": task.get("last_runtime_sec"),
        "status": task.get("last_status") or "ok",
        "output": task.get("last_output") or "",
        "raw_output": task.get("last_output_raw") or "",
        "error": task.get("last_error"),
    }
    task["run_history"] = [run]
    return task


def _format_task_run_header(run):
    finished_at = run.get("finished_at") or run.get("run_at") or ""
    status = run.get("status") or ""
    runtime = run.get("runtime_sec")
    if isinstance(runtime, (int, float)):
        runtime_text = f"{runtime:.2f}s"
    else:
        runtime_text = "n/a"
    parts = []
    if finished_at:
        parts.append(f"[{finished_at}]")
    parts.append(f"runtime={runtime_text}")
    if status:
        parts.append(f"status={status}")
    return " ".join(parts).strip()


def _build_task_history_text(run_history, field):
    if not run_history:
        return ""
    chunks = []
    for run in run_history:
        header = _format_task_run_header(run)
        body = run.get(field) or ""
        if not body and run.get("error"):
            body = str(run.get("error"))
        if header:
            chunks.append(f"{header}\n{body}".rstrip())
        else:
            chunks.append(str(body).rstrip())
    return "\n\n".join(chunks).strip()
