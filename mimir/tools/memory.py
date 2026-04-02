from norns import tool

from mimir import db


@tool(side_effect=True)
def remember(key: str, content: str) -> str:
    """Store a fact for later retrieval. Use a short descriptive key and the full fact as content."""
    db.upsert_memory(key, content)
    return f"Remembered: {key}"


@tool
def search_memory(query: str) -> str:
    """Search stored facts by keyword. Returns all facts where the key or content matches the query."""
    results = db.search_memories(query)
    if not results:
        return "No matching facts found."
    return "\n".join(f"[{key}] {content}" for key, content in results)
