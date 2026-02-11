"""Shared configuration, paths, and logging."""
import os
import pathlib
import json
import logging
import time

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('context_debug.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("Flask app starting up")
logger.info("=" * 60)

APP_START_TIME = time.time()

# Working directory and data paths
DEFAULT_CWD = os.environ.get("BILDIR_CWD", os.getcwd())
SESSION_STORE_PATH = os.environ.get("BILDIR_SESSION_STORE", os.path.join(DEFAULT_CWD, "sessions.json"))
HISTORY_STORE_PATH = os.environ.get("BILDIR_HISTORY_STORE", os.path.join(DEFAULT_CWD, "history.json"))
CLIENT_CONFIG_PATH = os.environ.get("BILDIR_CLIENT_CONFIG", os.path.join(DEFAULT_CWD, "client_config.json"))
LOG_STORE_PATH = os.environ.get("BILDIR_LOG_STORE", os.path.join(DEFAULT_CWD, "log.jsonl"))
MCP_JSON_PATH = os.environ.get("MCP_JSON_PATH", os.path.join(DEFAULT_CWD, "mcp.json"))
PROVIDER_CONFIG_PATH = os.environ.get("BILDIR_PROVIDER_CONFIG", os.path.join(DEFAULT_CWD, "providers", "config.toml"))
TASK_STORE_PATH = os.environ.get("BILDIR_TASK_STORE", os.path.join(DEFAULT_CWD, "tasks.json"))
ORCH_STORE_PATH = os.environ.get("BILDIR_ORCH_STORE", os.path.join(DEFAULT_CWD, "orchestrators.json"))
CONTEXT_DIR = os.path.join(DEFAULT_CWD, "context")
DEFAULT_PROVIDER = "codex"
SUPPORTED_PROVIDERS = {"codex", "copilot", "gemini", "claude"}
PROVIDER_ORDER = ["codex", "copilot", "gemini", "claude"]
DEFAULT_ORCH_BASE_PROMPT = (
    "Act as the manager across any task type. Always reply with the next concrete step toward the completion of the goal. "
    "If questions are asked, reply with the best solution or action required. Request manual runs and testing when you see fit, "
    "prioritize debugging and fixing before proceeding. Testing recommendation can include but are not limited to using pytest, "
    "MCP tools like playwright. Extreme cases where progress is stalling, execute tests yourself and report your findings. "
    "If you see any destructive or irreversible actions (i.e. deleting or overwriting files, dropping/truncating databases or tables) "
    "in the most recent conversation history from the assistant, then Use ask_human json format."
)
DEFAULT_ORCH_WORKER_PROMPT = "{goal}. Begin implementation immediately."
DEFAULT_ORCH_RULES = """- If the goal is achieved, return done
- If you can take another step toward the goal, send a message to continue the work
- Only ask_human if you truly need their input
- Review the conversation history to avoid repeating yourself
- If unsure what to do next, return done"""


def _load_client_config():
    path = pathlib.Path(CLIENT_CONFIG_PATH)
    if not path.exists():
        return {"copilot_permissions": "allow-all-paths", "copilot_enable_mcp": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"copilot_permissions": "allow-all-paths", "copilot_enable_mcp": False}
        if not data.get("copilot_permissions"):
            data["copilot_permissions"] = "allow-all-paths"
        if "copilot_enable_mcp" not in data:
            data["copilot_enable_mcp"] = False
        return data
    except Exception:
        return {"copilot_permissions": "allow-all-paths", "copilot_enable_mcp": False}


def _save_client_config(data):
    path = pathlib.Path(CLIENT_CONFIG_PATH)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_provider_config():
    return _load_client_config()


def _get_orchestrator_base_prompt(config):
    if not isinstance(config, dict):
        return DEFAULT_ORCH_BASE_PROMPT
    base = (config.get("orch_base_prompt") or "").strip()
    return base or DEFAULT_ORCH_BASE_PROMPT


def _get_orchestrator_worker_prompt(config):
    if not isinstance(config, dict):
        return DEFAULT_ORCH_WORKER_PROMPT
    text = (config.get("orch_worker_prompt") or "").strip()
    return text or DEFAULT_ORCH_WORKER_PROMPT


def _get_orchestrator_rules(config):
    if not isinstance(config, dict):
        return DEFAULT_ORCH_RULES
    rules = (config.get("orch_rules") or "").strip()
    return rules or DEFAULT_ORCH_RULES


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
            data = json.loads(gemini_config.read_text())
            models["gemini"] = data.get("model")
    except Exception:
        pass

    # Claude: read from ~/.claude/settings.json
    try:
        claude_config = pathlib.Path.home() / ".claude" / "settings.json"
        if claude_config.exists():
            data = json.loads(claude_config.read_text())
            models["claude"] = data.get("model")
    except Exception:
        pass

    return models


def _get_codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(pathlib.Path.home(), ".codex")
