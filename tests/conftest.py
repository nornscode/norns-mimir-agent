"""Let the suite run without Slack credentials or network access.

`mimir_agent.slack_bot` builds its Bolt `App` at import time, which needs a
bot token and calls Slack's `auth.test` to verify it. Import it once here
with a placeholder token and `auth.test` stubbed, so every later import gets
the cached module.
"""

import os
from unittest.mock import patch

os.environ["SLACK_BOT_TOKEN"] = os.environ.get("SLACK_BOT_TOKEN") or "xoxb-test"

with patch("slack_sdk.web.client.WebClient.auth_test", return_value=None):
    import mimir_agent.slack_bot  # noqa: F401
