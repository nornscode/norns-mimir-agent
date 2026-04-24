# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Mimir?

Mimir is a product knowledge AI agent built on [Norns](https://github.com/nornscode/norns) (a durable agent orchestrator). It answers product questions by searching GitHub repos, arbitrary URLs, and its own persistent memory, then responds with cited answers in Slack.

## Architecture

```
User Interface (Slack)
        ↓
  NornsClient (SDK)
        ↓
  Norns Server (orchestrator, runs separately)
        ↓
  Mimir Worker (this repo)
    ├── LLM calls (Anthropic Claude)
    └── Tool execution
        ├── GitHub search & file reading
        ├── Web URL fetching
        └── Memory (remember/search via PostgreSQL)
```

- **`norns.Norns`** — the worker class; connects via WebSocket to Norns server, receives tasks, executes agent steps
- **`norns.NornsClient`** — the client class; used by bots to send messages to the worker through Norns
- The Norns SDK is an editable dependency from `../norns-sdk-python`

## Key Entry Points

| Command | What it runs |
|---------|-------------|
| `uv run mimir-agent` | Unified: worker + Slack bot (if tokens configured) |
| `uv run mimir-worker` | Worker only |
| `uv run mimir-slack` | Slack bot only |

## Development Setup

1. Norns server must be running (default `http://localhost:4000` from `nornsctl dev`; set `NORNS_URL` to override)
2. PostgreSQL must be available (or use `docker compose up db` for Postgres on port 5433)
3. Copy `.env` and fill in required keys: `ANTHROPIC_API_KEY`, `NORNS_API_KEY`
4. `uv sync` to install dependencies
5. `uv run mimir` to start

## Docker

```bash
docker compose up          # Starts Postgres + Mimir
docker compose up db       # Postgres only (port 5433)
```

The Dockerfile copies `../norns-sdk-python` into the build context, so the SDK must exist at that path.

## Environment Variables

Required: `ANTHROPIC_API_KEY`, `NORNS_API_KEY`
Optional (enable features): `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN`, `GITHUB_TOKEN` + `GITHUB_REPOS`, `DATABASE_URL`

## Debugging with nornsctl

`nornsctl` is a CLI for inspecting the Norns runtime. Use it to check agent state, inspect runs, and view event logs when debugging issues.

```bash
nornsctl agents list                          # List all agents
nornsctl agents show <id>                     # Agent details
nornsctl agents status <id>                   # Live process state (idle, running, awaiting_llm, etc.)
nornsctl runs list [--agent <id>] [--limit N] # List runs
nornsctl runs show <id>                       # Run details + failure inspector
nornsctl runs events <id> [--json]            # Full event log for a run
nornsctl runs retry <id>                      # Retry a failed run
nornsctl conversations list <agent_id>        # List conversations
nornsctl conversations show <agent_id> <key>  # Conversation details
```

Configuration is via environment (already set up in `.envrc`):
- `NORNS_URL` — API base URL
- `NORNS_API_KEY` — bearer token

When debugging a failing run, start with `nornsctl runs show <id>` to check the failure inspector, then `nornsctl runs events <id> --json` to see the full event log.

## Code Conventions

- Package manager: `uv` (not pip). Use `uv sync`, `uv run`, `uv add`.
- Build system: hatchling
- Tools are decorated with `@tool` from the Norns SDK. Memory tools use `side_effect=True`.
- GitHub and URL-fetch tools truncate content at 8000 bytes.
- Database uses raw psycopg2 with a global connection (no ORM).
- Slack bot runs in a background thread (Bolt's threading model); the worker runs on the main asyncio loop.
