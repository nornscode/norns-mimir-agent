# Mimir v0 Implementation Plan

Last updated: 2026-03-26
Status: Active

## Goal
Ship a usable internal v0 of Mimir as a product-knowledge agent with durable execution on Norns.

## v0 Scope (must-have)

1. **Single worker process (Python)**
   - Runs via `norns.Norns`
   - Registers one agent definition and core tools

2. **Message ingress via SDK client**
   - Use `norns.NornsClient` to send messages to the agent
   - Support conversation keys (e.g., `slack:<channel_or_user_id>`)

3. **Core tools**
   - `search_knowledge(query)`
   - `remember(key, content)`
   - `search_memory(query)`

4. **Knowledge source (initial)**
   - Markdown docs from a local folder or Git repo
   - Simple text retrieval (keyword/BM25-style search; no vector DB required for v0)

5. **Integration surface (initial)**
   - CLI or minimal HTTP endpoint that forwards user questions into Norns

6. **Observability**
   - Inspect runs/events in Norns dashboard
   - Basic app logs for tool execution and failures

---

## Out of Scope (v0)

- Google Drive integration
- Full GitHub indexing pipeline
- Slack app production rollout
- Vector embeddings + reranking stack
- Advanced auth/team permissions

---

## Architecture (v0)

- **Norns**: orchestration, checkpoints, event log, retry/replay
- **Mimir Worker**: LLM calls + tool handlers
- **Mimir Client/Ingest**: receives user question, calls `NornsClient.send_message(...)`
- **Knowledge Store**: flat-file/docs index + `/remember` persistence backend

---

## Implementation Phases

### Phase 1 — Runtime wiring
- Create Python service skeleton
- Configure `Norns` worker connection + agent definition
- Implement a smoke-test tool and verify task dispatch works end-to-end

**Exit criteria:** worker can process one prompt and return response through Norns.

### Phase 2 — Knowledge tools
- Implement `search_knowledge` over markdown corpus
- Implement `/remember` persistence + retrieval helper (`search_memory`)
- Register tools with side-effect flags where needed

**Exit criteria:** agent can answer from docs and stored memory entries.

### Phase 3 — Ingress + conversation
- Add CLI/minimal HTTP wrapper that calls `NornsClient.send_message`
- Use deterministic conversation keys for multi-turn context
- Add basic error handling and timeouts

**Exit criteria:** users can ask follow-ups and retain conversation context.

### Phase 4 — Hardening
- Add unit tests for tools and schema
- Add integration test against local Norns docker stack
- Add logging + failure triage checklist

**Exit criteria:** stable internal demo for daily use.

---

## Suggested Repo Structure

```text
mimir/
  app.py
  mimir/
    worker.py
    client.py
    tools/
      search_knowledge.py
      remember.py
      search_memory.py
    knowledge/
      loader.py
      index.py
  tests/
  docs/
    design.md
    v0-plan.md
```

---

## Success Metrics (v0)

- 80%+ of internal product questions answered with useful references
- `/remember` facts are retrievable in subsequent conversations
- No lost runs on process restart (verified through Norns run history)

---

## Immediate Next 3 Tasks

1. Scaffold Python app with `Norns` worker + `NornsClient` configuration.
2. Implement markdown knowledge loader + `search_knowledge` tool.
3. Implement `/remember` persistence + `search_memory` tool and run end-to-end demo.
