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


# --- Slack event handlers ---

def handle_mention(body, say, client):
    """Handle @mentions — always respond."""
    _handle(body, say, client)


def handle_message(body, say, client):
    """Handle regular messages — only respond in DMs or threads we're already in."""
    global _bot_user_id
    event = body["event"]

    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
        return

    # Respond directly in DMs
    if event.get("channel_type") == "im":
        _handle(body, say, client)
        return

    # In channels, only respond to thread replies (not top-level messages)
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return

    # Check if we've already replied in this thread
    try:
        if _bot_user_id is None:
            _bot_user_id = client.auth_test()["user_id"]

        replies = client.conversations_replies(
            channel=event["channel"], ts=thread_ts, limit=20
        )
        bot_in_thread = any(
            msg.get("user") == _bot_user_id for msg in replies.get("messages", [])
        )
        if not bot_in_thread:
            return
    except Exception:
        return

    _handle(body, say, client)


MAX_FILE_SIZE = 8000  # same cap as other tools

TEXT_MIMETYPES = {
    "text/", "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/toml", "application/csv",
    "application/x-sh", "application/sql", "application/graphql",
}


def _is_text_file(mimetype: str, filetype: str) -> bool:
    """Check if a Slack file is a text-based file we can read."""
    if any(mimetype.startswith(t) for t in TEXT_MIMETYPES):
        return True
    # Slack filetypes for code/text
    text_filetypes = {
        "python", "javascript", "typescript", "ruby", "go", "rust", "java",
        "c", "cpp", "csharp", "swift", "kotlin", "scala", "php", "perl",
        "shell", "bash", "zsh", "fish", "powershell",
        "html", "css", "scss", "less", "xml", "svg",
        "json", "yaml", "toml", "ini", "conf", "cfg",
        "markdown", "text", "plain", "csv", "tsv", "log",
        "sql", "graphql", "proto", "dockerfile", "makefile",
        "elixir", "erlang", "haskell", "clojure", "lisp",
    }
    return filetype.lower() in text_filetypes


def _download_slack_files(event: dict, token: str, client=None) -> str:
    """Download text-based file attachments and return their contents."""
    files = event.get("files", [])
    if not files:
        return ""

    parts = []
    for f in files:
        name = f.get("name", "unknown")
        file_id = f.get("id")
        mimetype = f.get("mimetype", "")
        filetype = f.get("filetype", "")

        # Slack event payloads sometimes omit url_private_download.
        # Use files.info API to get the full file metadata when we have a client.
        if file_id and client and not f.get("url_private_download"):
            try:
                info = client.files_info(file=file_id)
                f = info.get("file", f)
                name = f.get("name", name)
                mimetype = f.get("mimetype", mimetype)
                filetype = f.get("filetype", filetype)
            except Exception:
                pass

        if not _is_text_file(mimetype, filetype):
            parts.append(f"\n--- Attached file: {name} (type: {mimetype}, not readable as text) ---")
            continue

        # Try inline content first (some file types have it in the API response)
        text = f.get("content") or f.get("plain_text") or f.get("preview")

        if not text:
            url = f.get("url_private_download")
            if not url:
                parts.append(f"\n--- Attached file: {name} (no download URL available) ---")
                continue

            try:
                import httpx
                resp = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                    timeout=15,
                )
                resp.raise_for_status()
                text = resp.text
            except Exception as e:
                parts.append(f"\n--- Attached file: {name} (failed to download: {e}) ---")
                continue

        if len(text) > MAX_FILE_SIZE:
            text = text[:MAX_FILE_SIZE] + f"\n\n... (truncated, {len(text)} chars total)"
        parts.append(f"\n--- Attached file: {name} ---\n{text}")

    return "\n".join(parts)


def _resolve_slack_links(text: str, client) -> str:
    """Expand Slack message links into their actual content.

    Slack formats links as <https://workspace.slack.com/archives/C123/p456|optional label>.
    We extract the channel + timestamp and fetch the message via the API.
    """
    slack_link_re = re.compile(
        r"<(https?://[^/]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:\?[^|>]*)?)(?:\|[^>]*)?>" 
    )

    def _expand(match):
        url = match.group(1)
        channel_id = match.group(2)
        # Slack encodes ts as p<ts_without_dot> — insert dot before last 6 digits
        raw_ts = match.group(3)
        ts = raw_ts[:-6] + "." + raw_ts[-6:] if len(raw_ts) > 6 else raw_ts

        try:
            result = client.conversations_history(
                channel=channel_id, latest=ts, inclusive=True, limit=1
            )
            msgs = result.get("messages", [])
            if msgs:
                msg = msgs[0]
                msg_text = msg.get("text", "")
                user = msg.get("user", "unknown")
                # Try to resolve user name
                try:
                    info = client.users_info(user=user)
                    user = info["user"].get("real_name") or info["user"].get("name", user)
                except Exception:
                    pass
                parts = [f"[Slack message from {user}: {msg_text}]"]
                # Also fetch any files attached to the linked message
                file_contents = _download_slack_files(msg, config.SLACK_BOT_TOKEN, client=client)
                if file_contents:
                    parts.append(file_contents)
                return "\n".join(parts)
        except Exception:
            pass
        return url

    return slack_link_re.sub(_expand, text)


def _resolve_slack_file_links(text: str, client) -> str:
    """Expand Slack file links into their content.

    Slack formats file links as <https://workspace.slack.com/files/U.../F.../name|label>.
    We fetch the file info via API and download the content.
    """
    file_link_re = re.compile(
        r"(?:<)?(https?://[^/]+\.slack\.com/files/[A-Z0-9]+/([A-Z0-9]+)/[^|>\s]+)(?:\|[^>]*)?>?"
    )

    def _expand(match):
        url = match.group(1)
        file_id = match.group(2)
        try:
            result = client.files_info(file=file_id)
            f = result.get("file", {})
            name = f.get("name", "unknown")
            mimetype = f.get("mimetype", "")
            filetype = f.get("filetype", "")
            if not _is_text_file(mimetype, filetype):
                return f"[Slack file: {name} (type: {mimetype}, not readable as text)]"

            # Some file types have content inline in the API response
            content = f.get("content") or f.get("plain_text") or f.get("preview")

            # Otherwise download via url_private_download
            if not content:
                download_url = f.get("url_private_download")
                if not download_url:
                    return f"[Slack file: {name} (no direct download URL available)]"

                import httpx
                resp = httpx.get(
                    download_url,
                    headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
                    follow_redirects=True,
                    timeout=15,
                )
                resp.raise_for_status()
                content = resp.text

            if len(content) > MAX_FILE_SIZE:
                content = content[:MAX_FILE_SIZE] + f"\n\n... (truncated, {len(content)} chars total)"
            return f"\n--- Slack file: {name} ---\n{content}"
        except Exception as e:
            return f"[Slack file {file_id}: failed to fetch ({e})]"

    return file_link_re.sub(_expand, text)


def _resolve_project(channel: str) -> str | None:
    """Look up the project for a Slack channel. Returns None if unmapped."""
    try:
        from mimir_agent import db
        return db.get_project_for_channel(channel)
    except Exception:
        return None


def _fetch_thread_context(
    channel: str, thread_ts: str, current_ts: str, client, bot_user_id: str | None
) -> str | None:
    """Fetch the prior messages in a thread so the agent sees the full context
    on its first invocation. Returns None if the bot has already replied in
    this thread (Norns already has the history) or if there's nothing to add.
    """
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts, limit=100)
    except Exception:
        return None

    msgs = result.get("messages", [])
    prior = [m for m in msgs if m.get("ts") != current_ts]
    if not prior:
        return None

    # If the bot has already replied in this thread, Norns has the conversation
    # state — re-including thread history would duplicate it.
    if bot_user_id and any(m.get("user") == bot_user_id for m in prior):
        return None

    lines = []
    for m in prior:
        if m.get("bot_id") or m.get("subtype"):
            continue
        text = m.get("text", "").strip()
        if not text:
            continue
        user_id = m.get("user", "unknown")
        try:
            info = client.users_info(user=user_id)
            user = info["user"].get("real_name") or info["user"].get("name", user_id)
        except Exception:
            user = user_id
        # Strip raw <@U...> mention markup so it reads naturally
        text = re.sub(r"<@\w+>", "", text).strip()
        lines.append(f"{user}: {text}")

    if not lines:
        return None

    return "[Earlier messages in this thread, for context]\n" + "\n\n".join(lines)


def _handle(body, say, client):
    global _bot_user_id
    event = body["event"]

    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
        return

    channel = event["channel"]
    thread_ts = event.get("thread_ts", event["ts"])
    user_text = event.get("text", "")

    # Strip only the bot's own mention, resolve other @mentions to names
    if _bot_user_id is None:
        try:
            _bot_user_id = client.auth_test()["user_id"]
        except Exception:
            pass
    if _bot_user_id:
        user_text = user_text.replace(f"<@{_bot_user_id}>", "").strip()
    else:
        user_text = re.sub(r"<@\w+>", "", user_text, count=1).strip()

    # Resolve remaining <@U...> mentions to display names
    def _resolve_mention(match):
        uid = match.group(1)
        try:
            info = client.users_info(user=uid)
            name = info["user"].get("real_name") or info["user"].get("name", uid)
            return f"@{name}"
        except Exception:
            return match.group(0)

    user_text = re.sub(r"<@(\w+)>", _resolve_mention, user_text)

    # Expand Slack message links into their content (including their attachments)
    user_text = _resolve_slack_links(user_text, client)

    # Expand Slack file links into their content
    user_text = _resolve_slack_file_links(user_text, client)

    # Download attached files on the current message
    file_contents = _download_slack_files(event, config.SLACK_BOT_TOKEN, client=client)
    if file_contents:
        user_text = user_text + file_contents

    if not user_text:
        return

    # If this is a reply in an existing thread, pull in the prior messages so
    # the agent has the full conversation on first invocation. Skipped if the
    # bot has already participated (Norns has the history).
    if event.get("thread_ts") and event["thread_ts"] != event["ts"]:
        thread_context = _fetch_thread_context(
            channel, thread_ts, event["ts"], client, _bot_user_id
        )
        if thread_context:
            user_text = f"{thread_context}\n\n[Your task / the latest message]\n{user_text}"

    # Resolve channel → project and prepend context
    project = _resolve_project(channel)
    if project:
        user_text = f"[channel_id={channel}, project={project}] {user_text}"
    else:
        user_text = f"[channel_id={channel}] {user_text}"

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


def handle_channel_invite(body, client):
    """Post a short hello when the bot is invited to a channel."""
    global _bot_user_id
    event = body["event"]
    try:
        if _bot_user_id is None:
            _bot_user_id = client.auth_test()["user_id"]
    except Exception:
        return

    # Only react when the bot itself just joined
    if event.get("user") != _bot_user_id:
        return

    channel = event.get("channel")
    if not channel:
        return

    try:
        client.chat_postMessage(
            channel=channel,
            text=(
                "Hi! I'm Mimir — I answer product questions by searching "
                "connected GitHub repos and web pages. @-mention me and I'll "
                "walk you through adding your project's sources."
            ),
        )
    except Exception as e:
        logger.error(f"Failed to post channel-invite greeting: {e}")


app.event("app_mention")(handle_mention)
app.event("message")(handle_message)
app.event("member_joined_channel")(handle_channel_invite)


def main():
    if not config.SLACK_BOT_TOKEN or not config.SLACK_APP_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
    logger.info("Starting Mimir Slack bot")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
