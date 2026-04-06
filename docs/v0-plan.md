# Mimir v0 Plan (Updated)

Last updated: 2026-03-30
Status: In progress

## Goal
Ship a reliable internal v0.1 for product knowledge support using Norns orchestration.

## Completed foundation

- Worker runtime wired to Norns
- Multi-source tools (GitHub, Google Docs, Figma, release notes)
- Persistent vector memory (`remember`, `search_memory`)
- Slack and Discord adapter scaffolding
- Docker + Postgres local stack

## Remaining v0.1 goals

1. **Quality pass**
   - Improve citation consistency in final answers
   - Add source confidence hints where possible

2. **Reliability pass**
   - Normalize tool errors into user-safe messages
   - Add retry/backoff for flaky external APIs

3. **Integration test pass**
   - End-to-end tests covering:
     - message ingress
     - tool execution
     - memory write/search
     - response delivery

4. **Docs + DX pass**
   - Keep README and env docs accurate
   - Add one deterministic demo script for onboarding

## Out of scope (v0.1)

- Full production auth/permissions model
- Deep retrieval ranking/LLM-eval harness
- Full GitHub repo indexing pipeline
- Advanced dashboard/analytics UI

## Success criteria

- Internal users can reliably ask product questions and get source-backed answers
- Memory retrieval improves repeated-query quality
- Runs are debuggable via Norns event timeline
- Local setup to first answer works in <10 minutes
