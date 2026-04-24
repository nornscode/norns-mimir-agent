# Mimir Design

Mimir is a Slack-based product-knowledge agent built on [Norns](https://github.com/nornscode/norns). It's the reference implementation for Norns: small enough to read end-to-end, production-shaped enough to be useful.

## Runtime model

- **Norns** is the orchestrator. It owns conversation state (keyed by `slack:<channel>:<thread>`), durable runs, retries, and the event timeline.
- **Mimir worker** is a Python process that connects to Norns over WebSocket. It executes `llm_task` (Claude calls) and `tool_task` (the tools listed below).
- **Slack adapter** runs in the same process — it receives Slack events, forwards user messages into Norns via `NornsClient.send_message`, and posts the agent's reply back to the originating thread.

If the worker crashes mid-tool-call, Norns replays the run on the next worker from the last checkpoint. No duplicate side effects, no lost context. This is the load-bearing reason to use Norns.

## Tool surface

- **GitHub:** `search_github`, `read_github_file`, `list_github_prs`, `read_github_pr`, `list_github_commits`, `list_github_branches`
- **Web:** `read_url` (fetch arbitrary pages on demand)
- **Sources:** `connect_source`, `disconnect_source`, `list_sources`
- **Memory:** `remember`, `search_memory` (vector-backed via pgvector)
- **Release notes:** `draft_release_notes` (over GitHub PRs/releases)

## Memory model

- Postgres table `memories` with `pgvector` embeddings (`all-MiniLM-L6-v2`, 384 dim).
- `remember` upserts by key, embeds content + key together, replaces on conflict.
- `search_memory` is cosine-similarity over the HNSW index, with a recency fallback when the corpus is unembedded.

## Source model

- Postgres table `sources` with `(type, identifier, label, is_default)`.
- **Default sources** are seeded on every `db.init()` regardless of env: `nornscode/norns-mimir-agent` and `nornscode/norns`. They can't be removed via `disconnect_source`. The point: every fresh Mimir can answer questions about itself and Norns out of the box.
- **User-registered sources** come from `connect_source` (the bot's tool, called by the LLM during onboarding) or env vars (`GITHUB_REPOS`).
- The "no user-registered sources" check (`db.user_source_count()`) is what triggers conversational onboarding on first @-mention.

## Onboarding

When `user_source_count() == 0`, the worker's system prompt includes an addendum that tells the LLM to walk new users through registering sources (GitHub repos, then URLs) instead of trying to answer their first question. The LLM drives the conversation; durability comes from Norns' conversation log, not a separate state machine.

## Current tradeoffs

- **Search quality is practical, not tuned.** GitHub code/issue search uses GitHub's native API; URL ingestion is one-shot HTML-to-text.
- **No ingestion pipeline.** Repos and URLs are searched on demand. URLs do get persisted into memory at registration time.
- **Single LLM provider.** `claude-sonnet-4` via the Anthropic API; no model abstraction layer.
- **System prompt is built once per worker start.** Sources and onboarding state in the prompt reflect a snapshot; the LLM has `list_sources` to discover fresh state mid-conversation.

## Out of scope for v0.1

- Discord transport, multi-workspace tenancy, hosted/managed Mimir, admin UI for sources, Google Docs / Drive integration, Figma integration, online eval pipeline (thread observation + feedback DMs), full repo ingestion / re-indexing pipeline.
