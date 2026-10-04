import os

from dotenv import load_dotenv

load_dotenv()

# Norns
NORNS_URL = os.environ.get("NORNS_URL", "http://localhost:4000")
NORNS_API_KEY = os.environ.get("NORNS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Slack
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

# GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOS = [r.strip() for r in os.environ.get("GITHUB_REPOS", "").split(",") if r.strip()]

# Figma — personal access token, required for figma_file sources.
# Generate at https://www.figma.com/settings → Personal access tokens.
FIGMA_TOKEN = os.environ.get("FIGMA_TOKEN", "")

# Embeddings (local)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))

# Memory ranking — the recency component of a memory's score halves every N days
MEMORY_HALF_LIFE_DAYS = float(os.environ.get("MEMORY_HALF_LIFE_DAYS", "90"))

# Model
MODEL = os.environ.get("MIMIR_MODEL", "claude-sonnet-5")

# The steward — the daily planning agent (see mimir_agent/steward.py).
# STEWARD_SLACK_CHANNEL is where its proposals are posted and answered; the
# bridge stays off entirely when it is unset.
STEWARD_AGENT = os.environ.get("STEWARD_AGENT", "norns-steward")
STEWARD_MODEL = os.environ.get("STEWARD_MODEL", "claude-opus-5")
STEWARD_SLACK_CHANNEL = os.environ.get("STEWARD_SLACK_CHANNEL", "")
# The agent approved work is handed to. It needs a checkout, so it runs on a
# gard on a real machine, not here.
STEWARD_CODER_AGENT = os.environ.get("STEWARD_CODER_AGENT", "sleipnir")
# How often the bridge asks Norns whether a steward run is waiting on an answer.
STEWARD_POLL_SECONDS = int(os.environ.get("STEWARD_POLL_SECONDS", "20"))

# Runtime
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/mimir_agent")

# Always-on default sources — registered on every install regardless of env.
# These are public repos; no PAT required for read access (GITHUB_TOKEN is
# used when present for rate-limit headroom).
# The steward surveys whatever is connected when it is given no explicit
# repo, so the whole project needs to be here, not just the two that answer
# most Slack questions.
DEFAULT_SOURCES: list[tuple[str, str, str]] = [
    ("github_repo", "nornscode/norns-mimir-agent", "Mimir itself"),
    ("github_repo", "nornscode/norns", "Norns durable runtime"),
    ("github_repo", "nornscode/sleipnir", "Sleipnir — the coding agent on Norns"),
    ("github_repo", "nornscode/norns-sdk-python", "Python worker SDK"),
    ("github_repo", "nornscode/norns-sdk-elixir", "Elixir worker SDK"),
    ("github_repo", "nornscode/nornsctl", "The Norns CLI"),
]
