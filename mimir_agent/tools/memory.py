from norns import tool

from mimir_agent import config, db
from mimir_agent.embeddings import get_embedding


@tool(side_effect=True)
def remember(key: str, content: str) -> str:
    """Store a fact for later retrieval. Use a short descriptive key and the full fact as content."""
    embedding = get_embedding(f"{key} {content}")
    db.upsert_memory(key, content, embedding)
    return f"Remembered: {key}"


@tool
def search_memory(query: str) -> str:
    """Search stored facts by semantic similarity. Returns the most relevant facts for the query."""
    query_embedding = get_embedding(query)
    results = db.search_memories(query_embedding)
    if not results:
        return "No matching facts found."
    return "\n".join(
        f"[{key}] (relevance: {similarity:.2f}) {content}"
        for key, content, similarity in results
    )


@tool(side_effect=True)
def reset_memory() -> str:
    """Reset all stored memories. Only available in dev mode."""
    if not config.DEV_MODE:
        return "Reset is only available in dev mode."
    count = db.clear_memories()
    return f"Cleared {count} memories."
