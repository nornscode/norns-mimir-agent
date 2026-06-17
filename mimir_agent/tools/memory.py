from norns import tool

from mimir_agent import config, db
from mimir_agent.embeddings import get_embedding


@tool(side_effect=True)
def remember(key: str, content: str, project: str = "default") -> str:
    """Store a fact for later retrieval. Use a short descriptive key and the full fact as content.

    The project parameter scopes the memory. Defaults to the current project.
    """
    embedding = get_embedding(f"{key} {content}")
    db.upsert_memory(key, content, embedding, project=project)
    return f"Remembered: {key} (project: {project})"


@tool
def search_memory(query: str, project: str = "") -> str:
    """Search stored facts by semantic similarity.

    By default searches the current project's memories first. Pass a specific
    project name to search that project, or "all" to search across all projects.
    """
    query_embedding = get_embedding(query)
    search_project = None if project == "all" else (project or None)
    results = db.search_memories(query_embedding, project=search_project)
    if not results:
        return "No matching facts found."
    return "\n".join(
        f"[{key}] (project: {proj}, relevance: {similarity:.2f}) {content}"
        for key, content, similarity, proj in results
    )


@tool(side_effect=True)
def reset_memory() -> str:
    """Reset all stored memories. Only available in dev mode."""
    if not config.DEV_MODE:
        return "Reset is only available in dev mode."
    count = db.clear_memories()
    return f"Cleared {count} memories."
