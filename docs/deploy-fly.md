# Deploying Mimir on Fly.io

One reference deploy. Mimir is cloud-agnostic — this guide just happens to be the one the maintainer uses. Swap Fly for anywhere that runs a Docker container and gives you a Postgres with `pgvector`.

## Prereqs

- [Fly CLI](https://fly.io/docs/flyctl/install/) (`fly` or `flyctl`) logged into your org
- A running Norns server (self-hosted, or a Fly app like `norns-runtime`)
- A Postgres with `pgvector` available. Fly Managed Postgres supports it via the Extensions UI.
- A Slack app — see the main [README](../README.md#2-create-the-slack-app) for the manifest and scopes

## 1) Database

On Fly Managed Postgres (`fly.io/dashboard/.../postgres`):

1. **Extensions** tab → find `vector` → **Install**.
2. **Databases** tab → **New database** → name it `mimir` (or whatever).
3. **Connect** tab → copy the connection string that points at the new database. Keep it handy for step 3.

If you're running regular Postgres elsewhere, just make sure `CREATE EXTENSION vector` succeeds in the target database — Mimir runs the extension-create at boot, but the user needs permission.

## 2) Create the Fly app

```bash
fly apps create <your-mimir-app> --org <your-org>
```

## 3) `fly.toml`

Mimir is designed to run as a single persistent worker Machine — no inbound HTTP (Slack uses Socket Mode; Norns uses a websocket client). Drop this in the repo root as `fly.toml` (gitignored):

```toml
app = '<your-mimir-app>'
primary_region = '<your-region>'   # e.g. 'yyz' — match your Norns region

[build]

[env]
  # If Norns runs in the same Fly org, use 6PN internal networking.
  # Otherwise, use the public Norns URL (https://...).
  NORNS_URL = 'http://<your-norns-app>.internal:4000'

[processes]
  app = 'uv run mimir-agent'

[[vm]]
  memory = '1gb'
  cpu_kind = 'shared'
  cpus = 1
```

Notes:
- **No `[http_service]`** — Mimir has no inbound HTTP. Fly won't run health checks, and the Machine won't auto-stop.
- **Memory: 1GB** is plenty. `fastembed` + ONNX runtime sit around 250-300MB steady-state.
- **Region: match Norns.** Low-latency 6PN matters more than geography; put Mimir in the same region as your Norns app.

## 4) Secrets

```bash
fly secrets set -a <your-mimir-app> \
  DATABASE_URL='postgres://mimir:...' \
  ANTHROPIC_API_KEY='sk-ant-...' \
  NORNS_API_KEY='nrn_...' \
  SLACK_BOT_TOKEN='xoxb-...' \
  SLACK_APP_TOKEN='xapp-...'
```

Optional (for private repos / higher GitHub rate limits):

```bash
fly secrets set -a <your-mimir-app> GITHUB_TOKEN='ghp_...'
```

## 5) Deploy

```bash
fly deploy -a <your-mimir-app>
fly scale count 1 -a <your-mimir-app>   # pin one Machine; no http_service = no autoscale
fly logs -a <your-mimir-app>            # tail
```

You're looking for `Starting Mimir Slack bot` and the Bolt Socket Mode connection log. No `psycopg2.OperationalError`, no `ConnectionRefused`.

## 6) Verify in Slack

- `/invite @Mimir` in any channel → one-line hello posts immediately.
- `@Mimir what's a tool in Norns?` → answers from the default `nornscode/norns` source.
- `@Mimir hello` → if no user-registered sources, kicks off onboarding.

## Troubleshooting

**Postgres connection refused.** The Fly MPG connection string uses `.flycast` — that's a private-network address, only reachable from apps in the same org. If you've got `postgres://…@localhost:…` or `@127.0.0.1:…`, you copied the wrong string.

**"extension 'vector' is not available".** The DB doesn't have pgvector enabled. Back to step 1 — Extensions tab.

**Bot silent in Slack.** Usually means Socket Mode isn't connected. Confirm `SLACK_APP_TOKEN` starts with `xapp-` and has `connections:write` scope. `fly logs` will show a Bolt disconnect/retry loop if the token is wrong.

**Mimir starts, then loops on "connection closed".** It's hitting Norns but the API key is rejected. Rotate/re-check `NORNS_API_KEY`.

**Rotating secrets.** `fly secrets set …` + `fly apps restart <your-mimir-app>`.
