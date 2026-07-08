# Changelog

All notable changes to Mimir are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
