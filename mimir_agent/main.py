import asyncio
import logging
import threading
import uuid

from norns.client import Norns

from mimir_agent import config, db
from mimir_agent.worker import build_agents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("mimir_agent")


async def run_worker():
    agents = build_agents()
    norns = Norns(config.NORNS_URL, api_key=config.NORNS_API_KEY)
    for agent in agents:
        norns._ensure_agent(agent)
    wid = f"python-worker-{uuid.uuid4().hex[:8]}"
    await norns._run_loop(agents, wid)


def run_slack():
    try:
        if not config.SLACK_BOT_TOKEN or not config.SLACK_APP_TOKEN:
            logger.info("SLACK_BOT_TOKEN/SLACK_APP_TOKEN not set, skipping Slack bot")
            return

        from slack_bolt.adapter.socket_mode import SocketModeHandler
        from mimir_agent import steward_bridge
        from mimir_agent.slack_bot import app

        # The bridge needs a Slack client and has to exist before the first
        # message arrives, so the handler can route steward answers.
        steward_bridge.start(app.client)

        logger.info("Starting Slack bot")
        handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
        handler.start()
    except Exception as e:
        logger.error(f"Slack bot failed to start: {e}", exc_info=True)


def main():
    db.init()

    # Slack Bolt runs its own threads, so start it in a background thread
    slack_thread = threading.Thread(target=run_slack, daemon=True)
    slack_thread.start()

    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
