import logging

from norns import Norns, Agent

from mimir import config
from mimir.tools import all_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

SYSTEM_PROMPT = """\
You are Mimir, a product knowledge assistant. Your job is to answer questions \
about the product by searching available knowledge sources.

You have access to these tools:
- search_github / read_github_file: Search code, docs, and issues in connected GitHub repos
- search_google_docs / read_google_doc: Search and read connected Google Docs
- remember / search_memory: Persistent memory backed by Postgres, shared across all conversations

When answering questions:
1. Search relevant sources first — don't guess.
2. Cite your sources (file paths, doc names, etc.).
3. If you learn something that might be asked again, use remember to save it.
4. If you don't know and can't find it, say so.
"""

agent = Agent(
    name="mimir",
    model="claude-sonnet-4-20250514",
    system_prompt=SYSTEM_PROMPT,
    tools=all_tools,
    mode="conversation",
    max_steps=20,
    on_failure="retry_last_step",
)


def main():
    from mimir import db
    db.init()

    norns = Norns(config.NORNS_URL, api_key=config.NORNS_API_KEY)
    norns.run(agent)


if __name__ == "__main__":
    main()
