from norns import tool

from mimir_agent import db

VALID_TYPES = {
    "github_repo": "GitHub repository (owner/repo format)",
    "google_doc": "Google Doc (document ID)",
    "figma_file": "Figma file (file key)",
}


@tool(side_effect=True)
def connect_source(source_type: str, identifier: str) -> str:
    """Connect a knowledge source. Types: github_repo (owner/repo), google_doc (doc ID), figma_file (file key)."""
    if source_type not in VALID_TYPES:
        return f"Unknown type '{source_type}'. Valid types: {', '.join(VALID_TYPES)}"
    added = db.add_source(source_type, identifier)
    if added:
        return f"Connected {source_type}: {identifier}"
    return f"Already connected: {identifier}"


@tool(side_effect=True)
def disconnect_source(source_type: str, identifier: str) -> str:
    """Disconnect a knowledge source."""
    removed = db.remove_source(source_type, identifier)
    if removed:
        return f"Disconnected {source_type}: {identifier}"
    return f"Not found: {source_type} {identifier}"


@tool
def list_sources() -> str:
    """List all connected knowledge sources."""
    sources = db.list_sources()
    if not sources:
        return "No sources connected."
    lines = []
    for source_type, identifier, label in sources:
        entry = f"- {source_type}: {identifier}"
        if label:
            entry += f" ({label})"
        lines.append(entry)
    return "\n".join(lines)
