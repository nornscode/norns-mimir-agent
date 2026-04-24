# Mimir

A product-knowledge bot for Slack. Mimir answers questions about your project by searching connected GitHub repos and web pages, with persistent memory across restarts. Built on [Norns](https://github.com/nornscode/norns), the durable agent runtime.

Every install ships knowing about [Norns](https://github.com/nornscode/norns) and Mimir itself, so you can test it before connecting any of your own sources.

## Try it without installing

[Join the public Mimir workspace on Slack](#) and ask Mimir a question. <!-- SLACK_INVITE_LINK -->

## Self-host (15 min)

### Prerequisites

- **Python 3.10+** and [`uv`](https://docs.astral.sh/uv/) (the project pins Python 3.14)
- **Docker** (for Postgres)
- **Norns** running locally:
  ```bash
  brew install nornscode/tap/nornsctl
  nornsctl dev
  ```
  Run `nornsctl dev status` in another terminal — note the **URL** and **API Key** it prints; you'll paste both into `.env` in step 3. See [the Norns README](https://github.com/nornscode/norns) for other install paths.
- **A Slack workspace** where you can create an app
- **An [Anthropic API key](https://console.anthropic.com/)**

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
| `DATABASE_URL` | optional | Defaults to the Postgres in `docker-compose.yml` |

### 4) Start

```bash
docker compose up
```

Compose brings up Postgres on port 5433 and the Mimir worker. The worker connects to your local Norns server, registers the agent, and starts the Slack listener.

> **First-run note**: Mimir downloads a small embedding model (`all-MiniLM-L6-v2`, ~80 MB) on first boot. Subsequent starts skip the download.

### 5) Invite the bot to a channel

In Slack, go to a channel and run `/invite @Mimir`. Mimir will post a one-line hello. **@-mention it** and it'll walk you through registering your project's sources (GitHub repos, public URLs). After onboarding, it answers questions in threads.

That's it. Total active work: about 15 minutes.

---

## Source credentials

Mimir's GitHub tools work **without any credentials** for public repos. Set `GITHUB_TOKEN` if you want:

- **Private repos**: token needs the `repo` scope
- **Higher rate limits**: any token works (~5,000 req/hr authenticated vs. ~60 unauthenticated)

URLs need no credentials — Mimir fetches them with a plain HTTP request.

## Entry points

- `mimir-agent` — combined: DB init + worker + Slack bot (if `SLACK_*` tokens set)
- `mimir-worker` — worker only
- `mimir-slack` — Slack adapter only

## Troubleshooting

**"Connection refused" on startup.** The worker can't reach Norns. Make sure `nornsctl dev` is running and `NORNS_URL` matches the URL from `nornsctl dev status`. The default `nornsctl dev` port is `4000`.

**"NORNS_API_KEY missing or invalid".** Run `nornsctl dev status` to see the local dev key, paste it into `.env`.

**Slack bot doesn't respond.** Check that both `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are set. The app-level token (`xapp-…`) is required for Socket Mode. Confirm the bot is invited to the channel.

**Slow first response.** First message in a process triggers the embedding-model download (~80 MB) and the GitHub repo cache warm-up. Subsequent messages are fast.

**Onboarding never triggers.** Onboarding fires when the database has no user-registered sources (defaults don't count). If you've previously connected a source, onboarding is skipped. Say `@mimir reconfigure` and Mimir will walk you through adding more.

**`docker compose up` fails on Postgres.** Port 5433 may be taken. Either stop the conflicting service or change the port mapping in `docker-compose.yml`.

## How it works

```
Slack ──► NornsClient ──► Norns server ──► Mimir worker (this repo)
                                                ├── LLM calls (Claude)
                                                └── Tools
                                                    ├── GitHub search/read
                                                    ├── URL fetch
                                                    └── Memory (Postgres + pgvector)
```

Conversations are keyed by Slack channel + thread, so threads are independent contexts. State is durable: if the worker crashes mid-tool-call, Norns replays the run on the next worker. If Postgres restarts, memory persists.

## Docs

- [`docs/design.md`](docs/design.md) — architecture, runtime model, tool surface

## License

MIT
