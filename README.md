<p align="center">
  <img src="docs/mimir-avatar.png" alt="Mimir" width="160" />
</p>

# Mimir

Product-knowledge bot for Slack. Connects to your GitHub repos and web pages, answers questions about your project, and remembers things across restarts. Built on [Norns](https://github.com/nornscode/norns).

Out of the box, Mimir knows about Norns and itself, so you can kick the tires before connecting your own sources.

## Self-host

Takes about 15 minutes.

### Prerequisites

- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) (the project pins Python 3.14)
- Docker (for Postgres)
- Norns running locally — `brew install nornscode/tap/nornsctl && nornsctl dev`. Run `nornsctl dev status` to get the URL and API key you'll need in step 3.
- A Slack workspace where you can create an app
- An [Anthropic API key](https://console.anthropic.com/)

### 1) Clone

```bash
git clone https://github.com/nornscode/norns-mimir-agent.git
cd norns-mimir-agent
```

### 2) Create the Slack app

1. Open <https://api.slack.com/apps?new_app=1> and choose **From a manifest**.
2. Pick your workspace, then paste this manifest:

   ```yaml
   display_information:
     name: Mimir
     description: Product knowledge agent. Searches connected repos and pages.
   features:
     bot_user:
       display_name: Mimir
       always_online: true
   oauth_config:
     scopes:
       bot:
         - app_mentions:read
         - channels:history
         - channels:read
         - chat:write
         - groups:history
         - groups:read
         - im:history
         - im:read
         - mpim:history
         - mpim:read
         - reactions:write
   settings:
     event_subscriptions:
       bot_events:
         - app_mention
         - message.channels
         - message.groups
         - message.im
         - message.mpim
         - member_joined_channel
     interactivity:
       is_enabled: false
     org_deploy_enabled: false
     socket_mode_enabled: true
     token_rotation_enabled: false
   ```

3. Click **Create**.
4. Under **OAuth & Permissions**, click **Install to Workspace**. Copy the **Bot User OAuth Token** (`xoxb-…`) — that's your `SLACK_BOT_TOKEN`.
5. Under **Basic Information** → **App-Level Tokens**, click **Generate Token and Scopes**. Add the `connections:write` scope, name it anything, and copy the token (`xapp-…`) — that's your `SLACK_APP_TOKEN`.

### 3) Configure env

```bash
cp .env.example .env
```

Fill in:

| Variable | Required | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | <https://console.anthropic.com/> |
| `NORNS_URL` | yes | `nornsctl dev status` (defaults to `http://localhost:4000`) |
| `NORNS_API_KEY` | yes | `nornsctl dev status` |
| `SLACK_BOT_TOKEN` | yes (for Slack) | Step 2.4 above |
| `SLACK_APP_TOKEN` | yes (for Slack) | Step 2.5 above |
| `GITHUB_TOKEN` | optional | <https://github.com/settings/tokens> — only needed for private repos or higher rate limits. Public repos work without it. |
| `FIGMA_TOKEN` | optional | <https://www.figma.com/settings> → Personal access tokens. Required to register Figma files as sources. |
| `DATABASE_URL` | optional | Defaults to the Postgres in `docker-compose.yml` |

### 4) Start

```bash
docker compose up
```

Compose brings up Postgres on port 5433 and the Mimir worker. The worker connects to your local Norns server, registers the agent, and starts the Slack listener.

First run downloads a small embedding model (`all-MiniLM-L6-v2`, ~80 MB). Subsequent starts skip this.

### 5) Invite the bot to a channel

In Slack, `/invite @Mimir` in a channel. @-mention it and it'll walk you through connecting your GitHub repos and URLs. After that, it answers questions in threads.

---

## Source credentials

GitHub tools work without credentials for public repos. Set `GITHUB_TOKEN` for private repos (needs `repo` scope) or higher rate limits (~5k req/hr vs ~60 unauthenticated). URLs need no credentials.

Figma files require `FIGMA_TOKEN` (personal access token from <https://www.figma.com/settings>). On `connect_source`, Mimir fetches the file at depth=2 and stores text content in memory. Use `read_figma_node` to drill into a specific frame when the snapshot isn't enough.

## Entry points

- `mimir-agent` — combined: DB init + worker + Slack bot (if `SLACK_*` tokens set)
- `mimir-worker` — worker only
- `mimir-slack` — Slack adapter only

## Troubleshooting

**"Connection refused" on startup** — Make sure `nornsctl dev` is running and `NORNS_URL` matches `nornsctl dev status`. Default port is `4000`.

**"NORNS_API_KEY missing or invalid"** — Run `nornsctl dev status` to get the key, paste it into `.env`.

**Slack bot doesn't respond** — Both `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` need to be set. The app-level token (`xapp-…`) is required for Socket Mode. Make sure the bot is invited to the channel.

**Slow first response** — First message triggers the embedding model download and GitHub repo cache warm-up. After that it's fast.

**Onboarding never triggers** — Onboarding only fires when there are no user-registered sources. If you've already connected one, say `@mimir reconfigure` to add more.

**`docker compose up` fails on Postgres** — Port 5433 may be taken. Stop the conflicting service or change the port in `docker-compose.yml`.

## How it works

```
Slack ──► NornsClient ──► Norns server ──► Mimir worker (this repo)
                                                ├── LLM calls (Claude)
                                                └── Tools
                                                    ├── GitHub search/read
                                                    ├── URL fetch
                                                    └── Memory (Postgres + pgvector)
```

Conversations are keyed by Slack channel + thread, so each thread is its own context. If the worker crashes mid-tool-call, Norns replays the run on the next worker that connects.

## Docs

- [`docs/design.md`](docs/design.md) — architecture, runtime model, tool surface
- [`docs/deploy-fly.md`](docs/deploy-fly.md) — reference deploy on Fly.io

## License

MIT
