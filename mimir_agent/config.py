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

# Embeddings (local)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))

# Runtime
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/mimir_agent")

# Always-on default sources — registered on every install regardless of env.
# These are public repos; no PAT required for read access (GITHUB_TOKEN is
# used when present for rate-limit headroom).
DEFAULT_SOURCES: list[tuple[str, str, str]] = [
    ("github_repo", "nornscode/norns-mimir-agent", "Mimir itself"),
    ("github_repo", "nornscode/norns", "Norns durable runtime"),
]
