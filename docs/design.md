# Mimir — Product Knowledge Agent

## Overview

Mimir is a proof-of-concept AI agent that serves as a company's "source of truth" for product information. It answers questions about how features work, when they'll be released, how to enable feature flags, and other product knowledge.

Built on [norns](https://github.com/amackera/norns), a durable agent runtime, using the [norns Python SDK](https://github.com/amackera/norns-sdk-python).

## Architecture

Mimir runs as a **norns worker** — it connects to a norns server via WebSocket and handles LLM inference and tool execution. Norns manages orchestration, durability, checkpointing, and state.

```
User (Slack, API, etc.)
  │
  ▼
Mimir integration layer ──POST /agents/:id/messages──► Norns Server
                                                          │
                                                          │ dispatches tasks
                                                          ▼
                                                    Mimir Worker (Python)
                                                      ├── LLM calls (Anthropic)
                                                      └── Tool execution
                                                           ├── search_knowledge
                                                           ├── search_github
                                                           ├── search_drive
                                                           └── remember
```

The worker handles two types of tasks from norns:
- **llm_task** — calls the Anthropic API with the agent's system prompt and messages
- **tool_task** — executes one of Mimir's tools (search docs, fetch from GitHub, etc.)

### Sending messages to the agent

The norns Python SDK supports both roles:

- **`Norns`** (worker) — connects via WebSocket, registers agent + tools, handles `llm_task` / `tool_task` dispatches
- **`NornsClient`** (client) — sends messages, queries runs/events, streams via WebSocket

Mimir uses `NornsClient` for inbound integrations (Slack, CLI, API) to send messages and poll for results.

## Knowledge Sources

### 1. GitHub Repos
- Connect GitHub repositories as knowledge sources
- Agent can search repo contents (code, docs, READMEs, issues)
- Use GitHub API for fetching content

### 2. Google Drive
- Link individual Google Drive documents or entire drives
- Agent can search and read document contents
- Use Google Drive API for access

### 3. Markdown Files
- Upload or link markdown files as knowledge sources
- Direct text content for the agent to search

### 4. Slash Command Memory (`/remember`)
- Users can tell Mimir to remember facts via a slash command
- e.g., `/remember Feature X launches on April 15th`
- Stored persistently, searchable by the agent
- Allows ad-hoc knowledge that doesn't live in any document

## Ingestion & Retrieval

**TBD** — key design question:

- **Index & search (RAG):** Ingest content upfront into a vector store/search index, agent searches via tool
- **Live fetch:** Agent calls APIs on demand each time a question comes in
- **Hybrid:** Index large corpora (full repos, drives), live fetch for specific lookups

## Status

Design complete for v0 scope. Open questions for future iterations:
- Ingestion strategy (RAG vs live fetch vs hybrid) for large corpora
- Storage backend for `/remember` command data
- Authentication model for GitHub and Google Drive integrations

See [v0-plan.md](v0-plan.md) for the implementation plan.
