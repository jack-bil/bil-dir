"""MCP configuration helpers."""
import json
import pathlib
from utils.config import MCP_JSON_PATH, PROVIDER_CONFIG_PATH


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
    path = pathlib.Path(PROVIDER_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return str(path)
