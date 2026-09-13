# Changelog

All notable changes to Mimir are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **norns-sdk 0.2.0 → 0.8.0.** Six releases of worker-side protocol had
  landed without Mimir taking any of them, against a core that auto-deploys
  on every push. What was missing, worst first:
  - **0.5.0 — worker-side prompt composition, kind rendering, `final_output`.**
    Since the opaque-content change core writes no prose for the model and
    expects the worker to compose the system prompt from the task envelope
    and report `final_output`. A 0.2.0 worker does neither.
  - **0.6.0 — compaction.** Mimir runs `mode="conversation"` with a
    50-message window, so long Slack threads are exactly the case compaction
    exists for, and `purpose: "compact"` tasks were unserved.
  - **0.4.0 — graceful drain on SIGTERM.** Every Fly deploy killed
    in-flight tool calls instead of letting them finish.
  - **0.3.1 — concurrent task execution.** Tasks ran one at a time: a slow
    web fetch blocked every other Slack thread on the connection.
  - **0.8.0 — idempotency.** A tool call re-dispatched after core loses the
    result no longer runs the side effect twice.

### Fixed
- `db._get_conn()` takes a lock. Tasks run concurrently from 0.3.1, so two
  threads could both find the connection unset and both open one. psycopg2
  serialises statements on a shared connection and every caller takes its
  own cursor, so the connection itself was always safe to share — the race
  was only in creating it.

## [0.2.0] - 2026-07-07

First tagged release. The public v0.1 milestone shipped onboarding and default
sources; 0.2.0 marks the point where the model-resolution issues are fully
resolved upstream and the deploy pipeline works end to end.

### Added
- Multi-project support: channels map to projects via `set_channel_project`,
  and memory/sources are scoped per project (`project="all"` searches across).
- Figma file support as a connectable source, with a depth cap on ingestion.
- Configurable model via `MIMIR_MODEL`.
- Slack thread context: the bot fetches prior thread messages on first
  invocation so replies have history.
- Slack file and message-link resolution — downloads attachments and inlines
  referenced messages into the prompt.
- GitHub Actions auto-deploy to Fly.io on push to `main`.

### Changed
- Responses are now radically concise — no preamble, no follow-up questions.
- Context window raised from 20 to 50 to keep tool-call pairs intact.
- Swapped sentence-transformers for fastembed (faster cold start, smaller image).

### Fixed
- Model alias resolution: pin to a concrete model name so the server no longer
  resolves `-latest` to a stale alias.
- Removed the SDK model-override monkey-patch now that the underlying agent-def
  caching bug is fixed upstream (nornscode/norns#6).
- `fly.toml` is now tracked so the auto-deploy workflow has app config — the
  first successful auto-deploy.
- Memory unique constraint corrected to `(key, project)`; dropped stray
  NOT NULL constraints added by the Norns server.
- Slack: fixed double replies, `@mention` stripping, and file downloads that
  lacked `url_private_download` in the event payload.

### Known issues
- Deleting a conversation while its agent process is live can wedge the thread
  until the process stops on its own (nornscode/norns#8, upstream).

[0.2.0]: https://github.com/nornscode/norns-mimir-agent/releases/tag/v0.2.0
