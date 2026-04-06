# Mimir Design (Current)

## Overview

Mimir is a production-leaning product-knowledge agent built on Norns.
It is designed to answer internal product questions by combining live source retrieval with durable memory.

## Runtime model

- Norns is the orchestrator (durable runs, retries, event timeline).
- Mimir runs as a worker process and executes:
  - `llm_task`
  - `tool_task`

## Ingress channels

- Slack adapter
- Discord adapter
- Any client that can call Norns message APIs

## Tool surface

- `search_github` / `read_github_file`
- `search_google_docs` / `read_google_doc`
- `search_figma` / `render_figma_frame`
- `remember` / `search_memory` (vector-backed)
- `draft_release_notes`

## Memory model

- Postgres table with pgvector embeddings
- semantic search over remembered facts
- backfill support for legacy rows without embeddings

## Current tradeoffs

- Search quality is practical but not heavily tuned (v0.1)
- Source connectors are API-driven and credential-managed by env
- Conversation quality depends on tool coverage and prompt discipline

## Near-term architecture priorities

1. Tighten source citation quality in responses
2. Add stronger tool error normalization/retries
3. Add integration tests across Slack/Discord -> Norns -> worker loop
4. Improve memory dedupe/update heuristics
