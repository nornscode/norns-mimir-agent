import logging

from norns import Norns, Agent

from mimir_agent import config
from mimir_agent.tools import all_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

SYSTEM_PROMPT = """\
You are Mimir, a product knowledge assistant. Answer questions by searching \
available knowledge sources. Be extremely brief — one or two sentences when \
possible. Never explain your process, what you searched, or offer follow-ups \
the user didn't ask for. Just give the answer.

Before answering, always search_memory first. Then search other sources if \
needed. Never say "I don't have information" without searching first.

When the user says "remember this", store their exact words — especially URLs \
and identifiers. Use descriptive keys (e.g. "norns_repo_url"). Before storing, \
check search_memory for duplicates and reuse existing keys.

For release notes, use draft_release_notes (not search_github).

Always do tool calls first, then respond in a separate message. Cite sources \
briefly (file path or doc name).

Formatting: use Slack mrkdwn. *bold*, _italic_, <url|label> for links. \
Never wrap URLs in underscores or other formatting. Use dashes for lists.
"""

def _build_sources_section() -> str:
    """Build a dynamic section listing connected knowledge sources."""
    from mimir_agent import db
    sources = db.list_sources()
    lines = ["\nConnected sources:"]
    default_rows = [row for row in sources if row[3]]
    user_rows = [row for row in sources if not row[3]]
    if default_rows:
        lines.append("- Always-on (every Mimir ships with these):")
        for source_type, identifier, label, _ in default_rows:
            entry = f"  - {source_type}: {identifier}"
            if label:
                entry += f" ({label})"
            lines.append(entry)
    if user_rows:
        lines.append("- User-registered:")
        for source_type, identifier, label, _ in user_rows:
            entry = f"  - {source_type}: {identifier}"
            if label:
                entry += f" ({label})"
            lines.append(entry)
    lines.append("- Memory: Postgres with semantic vector search")
    lines.append("- Web: can fetch any URL on demand")
    return "\n".join(lines)


ONBOARDING_PROMPT = """
Onboarding mode — this install has no user-registered sources yet. On the \
user's first message, do NOT try to answer their question immediately. \
Instead, walk them through conversational onboarding:

1. Brief hello. Mention you already know about Norns and Mimir itself (the \
default sources above) so they can test you right away. Then say that to \
answer questions about their project, you need to know where their context \
lives. A GITHUB_TOKEN may already be in the deploy env — it's only needed \
for private repos or rate-limit headroom; public repos work without it.
2. Ask: "Is there a GitHub repo I should monitor?" Collect URL(s) one at a \
time until they say no more. Call connect_source(source_type="github_repo", \
identifier="owner/repo") for each. Skippable.
3. Ask: "Any public URLs I should read — docs pages, blog posts, wikis?" \
For each URL, call connect_source(source_type="url", identifier="<url>") — \
this fetches the page and stores it in memory.
4. Summarize what's registered and tell them they can start asking \
questions now. Each source is searchable on demand.

If connect_source reports a credential problem or validation failure, relay \
the exact error to the user and point them at the README. Don't guess.

If the user later says "add source", "connect <something>", or \
"reconfigure", run the same flow again for the new source(s) only."""


def _build_onboarding_section() -> str:
    from mimir_agent import db
    if db.user_source_count() == 0:
        return ONBOARDING_PROMPT
    return ""


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT + _build_sources_section() + _build_onboarding_section()


def main():
    from mimir_agent import db
    db.init()

    agent = Agent(
        name="mimir-agent",
        model="claude-sonnet-4-20250514",
        system_prompt=_build_system_prompt(),
        tools=all_tools,
        mode="conversation",
        max_steps=40,
        on_failure="retry_last_step",
    )

    norns = Norns(config.NORNS_URL, api_key=config.NORNS_API_KEY)
    norns.run(agent)


if __name__ == "__main__":
    main()
