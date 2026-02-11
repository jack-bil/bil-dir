"""Gemini provider implementation."""
import os
import json
import pathlib
import shutil
import subprocess
import time
import queue
import threading

from utils.config import logger
from providers.base import _filter_debug_messages, _enqueue_output


def _resolve_gemini_path(config):
    configured = (config.get("gemini_path") or "").strip() if isinstance(config, dict) else ""
    if configured:
        return configured
    path = shutil.which("gemini") or shutil.which("gemini.cmd")
    if path:
        return path
    candidates = [
        pathlib.Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.cmd",
        pathlib.Path(os.environ.get("APPDATA", "")) / "npm" / "gemini",
        pathlib.Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Roaming" / "npm" / "gemini.cmd",
        pathlib.Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Roaming" / "npm" / "gemini",
        pathlib.Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "gemini.cmd",
        pathlib.Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "gemini",
    ]
    for candidate in candidates:
        try:
            if candidate and candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return None


def _ensure_gemini_policy():
    """Ensure Gemini CLI policy allows delegate_to_agent in non-interactive mode."""
    try:
        policy_dir = pathlib.Path.home() / ".gemini" / "policies"
        policy_dir.mkdir(parents=True, exist_ok=True)
        policy_path = policy_dir / "bil-dir.toml"
        policy_path.write_text(
            """[[rule]]
toolName = "delegate_to_agent"
decision = "allow"
priority = 100

[[rule]]
toolName = "brave-search"
decision = "allow"
priority = 90

[[rule]]
toolName = "brave_web_search"
decision = "allow"
priority = 90

[[rule]]
toolName = "brave_local_search"
decision = "allow"
priority = 90
""",
            encoding="utf-8",
        )
    except Exception:
        return


def _gca_available():
    """Detect Google Cloud Application Default Credentials (OAuth)."""
    if os.environ.get("GOOGLE_GENAI_USE_GCA"):
        return True
    home = pathlib.Path.home()
    gemini_oauth = home / ".gemini" / "oauth_creds.json"
    gemini_accounts = home / ".gemini" / "google_accounts.json"
    if gemini_oauth.exists() or gemini_accounts.exists():
        return True
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        adc = pathlib.Path(appdata) / "gcloud" / "application_default_credentials.json"
        if adc.exists():
            return True
    adc_unix = home / ".config" / "gcloud" / "application_default_credentials.json"
    return adc_unix.exists()


def _get_gemini_api_key_from_settings(cwd=None):
    def _read_settings(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        auth = data.get("auth")
        if isinstance(auth, dict) and auth.get("type") == "api_key":
            return (auth.get("api_key") or "").strip()
        return ""

    if cwd:
        candidate = pathlib.Path(cwd) / ".gemini" / "settings.json"
        if candidate.exists():
            key = _read_settings(candidate)
            if key:
                return key
    home_settings = pathlib.Path.home() / ".gemini" / "settings.json"
    if home_settings.exists():
        return _read_settings(home_settings)
    return ""


def _run_gemini_exec(prompt, history_messages, config, timeout_sec=300, cwd=None, resume_session_id=None, resume_last=False, context_briefing=None):
    if not prompt or not isinstance(prompt, str):
        raise ValueError("prompt must be a non-empty string")

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

    _ensure_gemini_policy()

    args = [gemini_path]
    if resume_last:
        args.extend(["--resume", "latest"])

    env = os.environ.copy()
    if _gca_available():
        env["GOOGLE_GENAI_USE_GCA"] = "1"
        env.pop("GEMINI_API_KEY", None)
    else:
        if not env.get("GEMINI_API_KEY"):
            api_key = _get_gemini_api_key_from_settings(cwd)
            if api_key:
                env["GEMINI_API_KEY"] = api_key

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(input=prompt + "\n", timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError("gemini CLI timed out")
    if proc.returncode != 0:
        raise RuntimeError(_filter_debug_messages((stderr or stdout or "gemini CLI failed")).strip())
    return _filter_debug_messages((stdout or "").strip())


def _run_gemini_exec_stream(prompt, config, timeout_sec=300, cwd=None, on_output=None, on_error=None):
    gemini_path = _resolve_gemini_path(config)
    if not gemini_path:
        raise FileNotFoundError("gemini CLI not found")

    _ensure_gemini_policy()

    args = [gemini_path]
    env = os.environ.copy()
    if not env.get("GEMINI_API_KEY"):
        api_key = _get_gemini_api_key_from_settings(cwd)
        if api_key:
            env["GEMINI_API_KEY"] = api_key

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=env,
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
                raise RuntimeError("gemini CLI timed out")

    proc.wait()
    if proc.returncode != 0:
        combined = "\n".join(stderr_lines) or "\n".join(stdout_lines) or "gemini CLI failed"
        raise RuntimeError(_filter_debug_messages(combined).strip())

    output = _filter_debug_messages("\n".join(stdout_lines).strip())
    return output
