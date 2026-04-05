import os

# Norns
NORNS_URL = os.environ.get("NORNS_URL", "http://localhost:4001")
NORNS_API_KEY = os.environ.get("NORNS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Discord
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Slack
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

# GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOS = [r.strip() for r in os.environ.get("GITHUB_REPOS", "").split(",") if r.strip()]

# Google Docs
GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "")
GOOGLE_DOC_IDS = [d.strip() for d in os.environ.get("GOOGLE_DOC_IDS", "").split(",") if d.strip()]

# Embeddings (local)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))

# Figma
FIGMA_ACCESS_TOKEN = os.environ.get("FIGMA_ACCESS_TOKEN", "")
FIGMA_FILE_KEYS = [k.strip() for k in os.environ.get("FIGMA_FILE_KEYS", "").split(",") if k.strip()]

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/mimir_agent")
