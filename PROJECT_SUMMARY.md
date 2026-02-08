# Project Summary

Bil-dir is a local Flask app that orchestrates multiple AI CLIs (Codex, Copilot, Gemini, Claude) through a single UI. It provides named sessions, a master console, one-shot scheduled tasks, and orchestrators that manage sessions against a goal.

Key components
- Sessions: Named chat threads with per-provider session IDs and working directories.
- Master console: Aggregated feed across all sessions with `@@session-name:` routing.
- Tasks: One-shot prompts that can be scheduled and run independently.
- Orchestrators: Manager agents that supervise sessions and inject next-step prompts.
- MCP integration: Central configuration that syncs to provider-specific MCP configs.

Data and storage
- JSON state is stored in the project directory (and created on first run): `sessions.json`, `tasks.json`, `orchestrators.json`, `history.json`, `log.jsonl`, `client_config.json`, `mcp.json`.
- These files are git-ignored and regenerated if missing.

Health monitoring
- `/health/dashboard` provides a dashboard for providers, tasks, sessions, and MCP health.
- `/api/health/full` returns structured health data for automation.

Configuration
- `.env` is supported (copy from `.env.example`) for keys like `BRAVE_API_KEY` and `GEMINI_API_KEY`.
- `/config` UI allows editing provider paths, MCP servers, and orchestrator defaults.
