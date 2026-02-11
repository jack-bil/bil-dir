"""Claude provider implementation."""
import os
import re
import time
import queue
import threading
import subprocess
import pathlib
import shutil

from utils.config import logger
from providers.base import _filter_debug_messages, _enqueue_output


def _resolve_claude_path(config):
    return config.get("claude_path") or shutil.which("claude") or shutil.which("claude.cmd")


def _get_latest_claude_session_id(cwd=None, min_mtime=None, exact_only=False):
    """Get the most recent Claude session ID for a working directory."""
    import tempfile

    def normalize_for_match(s):
        return s.lower().replace(" ", "-").replace("_", "-")

    temp_dir = tempfile.gettempdir()
    claude_temp = os.path.join(temp_dir, "claude")
    claude_temp_exists = os.path.exists(claude_temp)

    uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    sessions = []

    if cwd and claude_temp_exists:
        cwd_encoded = normalize_for_match(cwd.replace(":", "-").replace("\\", "-").replace("/", "-"))
        search_dir = os.path.join(claude_temp, cwd_encoded)

        if os.path.exists(search_dir) and os.path.isdir(search_dir):
            items = os.listdir(search_dir)
            for dirname in items:
                path = os.path.join(search_dir, dirname)
                if os.path.isdir(path) and uuid_pattern.match(dirname):
                    mtime = os.path.getmtime(path)
                    if min_mtime is not None and mtime < min_mtime:
                        continue
                    sessions.append((dirname, mtime))
        elif not exact_only:
            dir_basename_normalized = normalize_for_match(os.path.basename(cwd))
            for workdir_name in os.listdir(claude_temp):
                workdir_normalized = normalize_for_match(workdir_name)
                if dir_basename_normalized in workdir_normalized:
                    workdir_path = os.path.join(claude_temp, workdir_name)
                    if not os.path.isdir(workdir_path):
                        continue
                    for dirname in os.listdir(workdir_path):
                        path = os.path.join(workdir_path, dirname)
                        if os.path.isdir(path) and uuid_pattern.match(dirname):
                            mtime = os.path.getmtime(path)
                            if min_mtime is not None and mtime < min_mtime:
                                continue
                            sessions.append((dirname, mtime))
    elif claude_temp_exists and not exact_only:
        for workdir_name in os.listdir(claude_temp):
            workdir_path = os.path.join(claude_temp, workdir_name)
            if not os.path.isdir(workdir_path):
                continue
            for dirname in os.listdir(workdir_path):
                path = os.path.join(workdir_path, dirname)
                if os.path.isdir(path) and uuid_pattern.match(dirname):
                    mtime = os.path.getmtime(path)
                    if min_mtime is not None and mtime < min_mtime:
                        continue
                    sessions.append((dirname, mtime))

    if not sessions:
        project_roots = []
        home_path = pathlib.Path.home()
        project_roots.append(home_path / ".claude" / "projects")
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            project_roots.append(pathlib.Path(user_profile) / ".claude" / "projects")
        home_env = os.environ.get("HOME")
        if home_env:
            project_roots.append(pathlib.Path(home_env) / ".claude" / "projects")

        for projects_root in project_roots:
            if not projects_root.exists():
                continue

            def collect_from_project_dir(project_dir):
                for entry in project_dir.iterdir():
                    if entry.is_file() and entry.suffix == ".jsonl":
                        name = entry.stem
                        if uuid_pattern.match(name):
                            mtime = entry.stat().st_mtime
                            if min_mtime is not None and mtime < min_mtime:
                                continue
                            sessions.append((name, mtime))

            if cwd:
                project_encoded = normalize_for_match(cwd.replace(":", "-").replace("\\", "-").replace("/", "-"))
                exact_project_dir = projects_root / project_encoded
                if exact_project_dir.exists() and exact_project_dir.is_dir():
                    collect_from_project_dir(exact_project_dir)
                elif not exact_only:
                    dir_basename_normalized = normalize_for_match(os.path.basename(cwd))
                    for project_dir in projects_root.iterdir():
                        if project_dir.is_dir():
                            project_name_normalized = normalize_for_match(project_dir.name)
                            if dir_basename_normalized in project_name_normalized:
                                collect_from_project_dir(project_dir)
            elif not exact_only:
                for project_dir in projects_root.iterdir():
                    if project_dir.is_dir():
                        collect_from_project_dir(project_dir)
            if sessions:
                break

    if not sessions and cwd and claude_temp_exists and not exact_only:
        for workdir_name in os.listdir(claude_temp):
            workdir_path = os.path.join(claude_temp, workdir_name)
            if not os.path.isdir(workdir_path):
                continue
            for dirname in os.listdir(workdir_path):
                path = os.path.join(workdir_path, dirname)
                if os.path.isdir(path) and uuid_pattern.match(dirname):
                    mtime = os.path.getmtime(path)
                    if min_mtime is not None and mtime < min_mtime:
                        continue
                    sessions.append((dirname, mtime))

    if not sessions:
        return None

    sessions.sort(key=lambda x: x[1], reverse=True)
    return sessions[0][0]


def _wait_for_claude_session_id(cwd, timeout_sec=2.0, interval_sec=0.1, min_mtime=None, exact_only=False):
    deadline = time.monotonic() + timeout_sec
    last_seen = None
    while time.monotonic() < deadline:
        last_seen = _get_latest_claude_session_id(cwd, min_mtime=min_mtime, exact_only=exact_only)
        if last_seen:
            return last_seen
        time.sleep(interval_sec)
    return last_seen


def _is_uuid(value):
    if not value or not isinstance(value, str):
        return False
    uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    return bool(uuid_pattern.match(value))


def _run_claude_exec(prompt, config, timeout_sec=300, cwd=None, resume_session_id=None, resume_last=False, context_briefing=None):
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
    args.append("--dangerously-skip-permissions")

    if _is_uuid(resume_session_id):
        args.extend(["--resume", resume_session_id])
    elif resume_last:
        args.append("--continue")

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        stdout, stderr = proc.communicate(input=prompt + "\n", timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError("claude CLI timed out")
    if proc.returncode != 0:
        raise RuntimeError(_filter_debug_messages((stderr or stdout or "claude CLI failed")).strip())

    output = _filter_debug_messages((stdout or "").strip())
    return _clean_claude_output(output)


def _run_claude_exec_stream(prompt, config, timeout_sec=300, cwd=None, on_output=None, on_error=None):
    claude_path = _resolve_claude_path(config)
    if not claude_path:
        raise FileNotFoundError("claude CLI not found")

    args = [claude_path, "--dangerously-skip-permissions"]
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if prompt:
        proc.stdin.write(prompt + "\n")
        proc.stdin.flush()
        proc.stdin.close()

    q = queue.Queue()
    t_out = threading.Thread(target=_enqueue_output, args=(proc.stdout, q, "stdout"))
    t_err = threading.Thread(target=_enqueue_output, args=(proc.stderr, q, "stderr"))
    t_out.daemon = True
    t_err.daemon = True
    t_out.start()
    t_err.start()

    stdout_lines = []
    stderr_lines = []
    start = time.monotonic()
    while True:
        try:
            label, line = q.get(timeout=0.25)
            line_text = line.rstrip("\n")
            if label == "stdout":
                if line_text:
                    stdout_lines.append(line_text)
                    if on_output:
                        on_output(line_text)
            else:
                if line_text:
                    stderr_lines.append(line_text)
                    if on_error:
                        on_error(line_text)
        except queue.Empty:
            if proc.poll() is not None:
                break
            if time.monotonic() - start > timeout_sec:
                proc.kill()
                raise RuntimeError("claude CLI timed out")

    proc.wait()
    if proc.returncode != 0:
        combined = "\n".join(stderr_lines) or "\n".join(stdout_lines) or "claude CLI failed"
        raise RuntimeError(_filter_debug_messages(combined).strip())

    output = _filter_debug_messages("\n".join(stdout_lines).strip())
    return _clean_claude_output(output)


def _clean_claude_output(text):
    if not text:
        return ""
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith("\u25cf") or line_stripped.startswith("\u2514"):
            continue
        if not line_stripped and cleaned_lines and not cleaned_lines[-1].strip():
            continue
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
