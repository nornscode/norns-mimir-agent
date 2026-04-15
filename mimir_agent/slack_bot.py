import logging
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from norns import NornsClient

from mimir_agent import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("mimir_agent.slack")

app = App(token=config.SLACK_BOT_TOKEN)
norns_client = NornsClient(config.NORNS_URL, api_key=config.NORNS_API_KEY)
_bot_user_id: str | None = None


def to_slack_mrkdwn(text: str) -> str:
    """Convert common Markdown patterns to Slack mrkdwn.

    Slack supports *bold* and _italic_, not Markdown **bold**.
    """
    if not text:
        return text

    out = text

    # Convert links: [text](url) -> <url|text>
    out = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"<\2|\1>", out)

    # Convert headings to bold lines
    out = re.sub(r"(?m)^\s*#{1,6}\s+(.+)$", r"*\1*", out)

    # Convert markdown bold/italic to Slack style
    out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out)
    out = re.sub(r"__(.+?)__", r"*\1*", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"_\1_", out)

    # Normalize list bullets to a cleaner glyph
    out = re.sub(r"(?m)^\s*[-*]\s+", "• ", out)

    # Keep output readable in Slack mobile
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    return out


def handle_mention(body, say, client):
    """Handle @mentions — always respond."""
    _handle(body, say, client)


def handle_message(body, say, client):
    """Handle regular messages — only respond in DMs or threads we're already in."""
    event = body["event"]

    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
        return

    # Always respond to DMs
    if event.get("channel_type") == "im":
        _handle(body, say, client)
        return

    # In channels, only respond to thread replies (not top-level messages)
    if not event.get("thread_ts"):
        return

    # Check if we've already replied in this thread
    try:
        global _bot_user_id
        if _bot_user_id is None:
            _bot_user_id = client.auth_test()["user_id"]

        replies = client.conversations_replies(
            channel=event["channel"], ts=event["thread_ts"], limit=20
        )
        bot_in_thread = any(
            msg.get("user") == _bot_user_id for msg in replies.get("messages", [])
        )
        if not bot_in_thread:
            return
    except Exception:
        return

    _handle(body, say, client)


def _handle(body, say, client):
    event = body["event"]

    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
        return

    channel = event["channel"]
    thread_ts = event.get("thread_ts", event["ts"])
    user_text = event.get("text", "")

    # Strip bot mention
    user_text = re.sub(r"<@\w+>", "", user_text).strip()
    if not user_text:
        return

    conversation_key = f"slack:{channel}:{thread_ts}"

    # Add thinking reaction
    try:
        client.reactions_add(channel=channel, timestamp=event["ts"], name="thinking_face")
    except Exception:
        pass

    try:
        result = norns_client.send_message(
            "mimir-agent",
            user_text,
            conversation_key=conversation_key,
            wait=True,
            timeout=120,
        )

        # Remove thinking reaction
        try:
            client.reactions_remove(channel=channel, timestamp=event["ts"], name="thinking_face")
        except Exception:
            pass

        if result.output:
            say(text=to_slack_mrkdwn(result.output), thread_ts=thread_ts)
        elif result.status == "completed":
            say(text="Done — but I didn't have anything to add beyond what I found.", thread_ts=thread_ts)
        else:
            say(text=f"Sorry, something went wrong (status: {result.status}).", thread_ts=thread_ts)

    except TimeoutError:
        try:
            client.reactions_remove(channel=channel, timestamp=event["ts"], name="thinking_face")
        except Exception:
            pass
        say(text="Sorry, that took too long. Please try again.", thread_ts=thread_ts)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        try:
            client.reactions_remove(channel=channel, timestamp=event["ts"], name="thinking_face")
        except Exception:
            pass
        say(text="Sorry, something went wrong.", thread_ts=thread_ts)


app.event("app_mention")(handle_mention)
app.event("message")(handle_message)


def main():
    if not config.SLACK_BOT_TOKEN or not config.SLACK_APP_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
    logger.info("Starting Mimir Slack bot")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
