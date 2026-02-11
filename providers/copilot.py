"""Copilot provider implementation."""
import os
import subprocess
import pathlib
import json
import shutil

from utils.config import logger
from core.mcp_manager import _load_mcp_json, _write_mcp_json_file
from providers.claude import _is_uuid


def _resolve_copilot_path(config):
    return config.get("copilot_path") or shutil.which("copilot") or shutil.which("copilot.cmd")


def _is_copilot_footer_line(line):
    if not line:
        return False
    stripped = line.strip()
    return (
        stripped.startswith("Total usage est:")
        or stripped.startswith("API time spent:")
        or stripped.startswith("Total session time:")
        or stripped.startswith("Total code changes:")
        or stripped.startswith("Breakdown by AI model:")
    )


def _strip_copilot_footer(text):
    if not text:
        return text
    lines = text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Total usage est:"):
            start_idx = i
            break
    if start_idx is None:
        return text
    tail = lines[start_idx:]
    if any(_is_copilot_footer_line(line) for line in tail):
        lines = lines[:start_idx]
    return "\n".join(lines).rstrip()


def _run_copilot_exec(prompt, cwd, config, extra_args=None, timeout_sec=300, resume_session_id=None, resume_last=False, context_briefing=None):
    logger.debug(f"[Context] _run_copilot_exec called with context={context_briefing is not None}, resume={resume_session_id}, last={resume_last}")
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

    if resume_session_id and _is_uuid(resume_session_id):
        args.extend(["--resume", resume_session_id])
    elif resume_last:
        args.append("--continue")

    copilot_permissions = (config.get("copilot_permissions") or "").strip()
    if copilot_permissions:
        args.append(f"--{copilot_permissions}")
    else:
        args.append("--allow-all-paths")

    copilot_model = (config.get("copilot_model") or "").strip()
    if copilot_model:
        args.extend(["--model", copilot_model])

    mcp_data = _load_mcp_json(config)
    if mcp_data and (config.get("copilot_enable_mcp") is True):
        mcp_path = _write_mcp_json_file(mcp_data)
        args.extend(["--additional-mcp-config", f"@{mcp_path}"])
    if extra_args:
        args.extend(extra_args)

    env = os.environ.copy()
    token = (config.get("copilot_token") or "").strip()
    token_env = (config.get("copilot_token_env") or "GH_TOKEN").strip() or "GH_TOKEN"
    if token:
        env[token_env] = token

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
        returncode = -1

    class ProcResult:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return ProcResult(returncode, stdout, stderr), args
