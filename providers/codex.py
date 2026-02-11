"""Codex provider implementation."""
import os
import subprocess
import json
import shutil
from utils.config import logger, _get_sandbox_mode, _get_provider_config, _get_codex_home
from providers.base import _filter_debug_messages


def _resolve_codex_path():
    return os.environ.get("CODEX_PATH") or os.environ.get("CODEX_BIN") or shutil.which("codex")


def _build_codex_args(codex_path, extra_args, json_events, resume_session_id, resume_last, prompt, context_briefing=None):
    """Build codex command args for stdin support."""
    if resume_session_id and str(resume_session_id).startswith("codex-"):
        # Synthetic IDs are for local tracking only; do not pass to codex resume.
        resume_session_id = None
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
    return args, prompt


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

    args, prompt = _build_codex_args(codex_path, extra_args, json_events, resume_session_id, resume_last, prompt, context_briefing)

    env = os.environ.copy()
    if not env.get("CODEX_HOME"):
        env["CODEX_HOME"] = _get_codex_home()
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
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError("codex exec timed out")

    if returncode != 0:
        raise RuntimeError(_filter_debug_messages((stderr or stdout or "codex exec failed")).strip())

    class ProcResult:
        def __init__(self, rc, out, err):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    return ProcResult(returncode, stdout, stderr), args


def _run_codex_exec_stream(prompt, cwd, extra_args=None, timeout_sec=300, resume_session_id=None, resume_last=False, json_events=True, context_briefing=None):
    if not prompt or not isinstance(prompt, str):
        raise ValueError("prompt must be a non-empty string")

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

    args, prompt = _build_codex_args(codex_path, extra_args, json_events, resume_session_id, resume_last, prompt, context_briefing)

    env = os.environ.copy()
    if not env.get("CODEX_HOME"):
        env["CODEX_HOME"] = _get_codex_home()
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
    return proc, args


def _extract_session_id(events):
    for evt in events:
        if not isinstance(evt, dict):
            continue
        if evt.get("type") == "session_id" and isinstance(evt.get("session_id"), str):
            return evt.get("session_id")
        for key in ("session_id", "sessionId", "session", "thread_id", "threadId", "thread_id"):
            val = evt.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _extract_codex_assistant_output(raw_text):
    if not raw_text:
        return raw_text
    assistant_text = []
    for line in raw_text.split("\n"):
        if line.startswith('{"type":'):
            try:
                event = json.loads(line)
                if event.get("type") == "item.completed":
                    item = event.get("item", {})
                    if item.get("type") == "agent_message" and item.get("text"):
                        assistant_text.append(item.get("text", ""))
                    elif item.get("type") == "message" and item.get("role") == "assistant":
                        for content in item.get("content", []):
                            if content.get("type") == "text":
                                assistant_text.append(content.get("text", ""))
            except Exception:
                pass
    if assistant_text:
        return "\n".join(assistant_text)
    return raw_text
