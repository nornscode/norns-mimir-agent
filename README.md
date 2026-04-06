# norns-mimir-agent

Mimir is a product-knowledge agent built on [Norns](https://github.com/amackera/norns).

It runs as a Norns worker and can answer questions using:
- GitHub repos
- Google Docs
- Figma files
- Persistent memory (`remember` / `search_memory`)
- Release-note drafting over merged PRs and releases

## Architecture

- **Norns** orchestrates runs, durability, retries, and event logs.
- **Mimir worker** executes LLM and tool tasks.
- **Slack/Discord adapters** send user messages into Norns and return responses.
- **Postgres + pgvector** stores long-term memory embeddings.

## Current Status

Active implementation (not just planning).

Implemented:
- Worker runtime (`mimir-worker`)
- Combined runtime entrypoint (`mimir-agent`)
- Slack and Discord adapters
- Vector memory storage/search
- GitHub / Google Docs / Figma / release-note tools

## Quickstart (local)

### 1) Prereqs
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Norns running locally (default `http://localhost:4001`)
- Docker (optional, for Postgres)

### 2) Configure env
```bash
cp .env.example .env
# fill required keys
```

### 3) Install
```bash
uv sync
```

### 4) Run Postgres (optional)
```bash
docker compose up -d db
```

### 5) Start agent runtime
```bash
uv run mimir-agent
```

Or run worker-only:
```bash
uv run mimir-worker
```

## Entry points

- `mimir-agent` — combined: DB init + worker + optional Slack/Discord
- `mimir-worker` — worker only
- `mimir-slack` — Slack adapter only
- `mimir-discord` — Discord adapter only

## Config

See `.env.example` for all supported environment variables.

## Docs

- `docs/design.md` — architecture and product shape
- `docs/v0-plan.md` — updated implementation roadmap and next milestones
- `docs/release-v0.1-checklist.md` — release checklist

## License

MIT
