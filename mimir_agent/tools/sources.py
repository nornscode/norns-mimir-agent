import logging

from norns import tool

from mimir_agent import config, db

logger = logging.getLogger(__name__)

VALID_TYPES = {
    "github_repo": "GitHub repository (owner/repo format)",
    "url": "Any web URL — its content will be fetched and stored in memory",
}


def _validate_github_repo(identifier: str) -> tuple[bool, str]:
    """Check that owner/repo resolves. Works unauthenticated for public repos."""
    if "/" not in identifier or identifier.count("/") != 1:
        return False, f"'{identifier}' is not in owner/repo format"
    try:
        from github import Github, GithubException

        client = Github(config.GITHUB_TOKEN) if config.GITHUB_TOKEN else Github()
        repo = client.get_repo(identifier)
        # Force a real API call by accessing a field
        _ = repo.full_name
        return True, ""
    except GithubException as e:
        msg = e.data.get("message", str(e)) if hasattr(e, "data") else str(e)
        if e.status == 404:
            return False, (
                f"GitHub repo '{identifier}' not found or not accessible. "
                f"If it's private, set GITHUB_TOKEN with repo scope in the env "
                f"(see README → GitHub credentials)."
            )
        if e.status == 403 and "rate limit" in msg.lower():
            return False, (
                "Hit GitHub rate limit while validating. Set GITHUB_TOKEN in "
                "the env for higher limits (see README → GitHub credentials)."
            )
        return False, f"GitHub error: {msg}"
    except Exception as e:
        return False, f"Could not reach GitHub: {e}"


def _ingest_url(url: str) -> tuple[bool, str]:
    """Fetch a URL and store its content as a memory entry."""
    from mimir_agent.tools.web import _fetch_one
    from mimir_agent.tools.memory import remember

    text = _fetch_one(url)
    if text.startswith("Failed to fetch "):
        return False, text

    # Store as a memory so search_memory can surface it later
    remember.handler(key=f"url:{url}", content=text)
    return True, ""


@tool(side_effect=True)
def connect_source(source_type: str, identifier: str, label: str = "") -> str:
    """Register a knowledge source. Validates access before adding.

    Types:
    - github_repo: owner/repo format (e.g. "nornscode/norns")
    - url: any web URL (its content is fetched and stored in memory)
    """
    if source_type not in VALID_TYPES:
        return (
            f"Unknown source type '{source_type}'. "
            f"Valid types: {', '.join(VALID_TYPES)}"
        )

    identifier = identifier.strip()
    if not identifier:
        return "Identifier is required."

    validators = {
        "github_repo": _validate_github_repo,
        "url": _ingest_url,
    }
    ok, err = validators[source_type](identifier)
    if not ok:
        return err

    added = db.add_source(source_type, identifier, label=label or None)
    if added:
        return f"Connected {source_type}: {identifier}"
    return f"Already connected: {source_type} {identifier}"


@tool(side_effect=True)
def disconnect_source(source_type: str, identifier: str) -> str:
    """Disconnect a user-registered knowledge source. Default sources cannot be removed."""
    removed = db.remove_source(source_type, identifier)
    if removed:
        return f"Disconnected {source_type}: {identifier}"
    return f"Not found (or is a default and cannot be removed): {source_type} {identifier}"


@tool
def list_sources() -> str:
    """List all connected knowledge sources (defaults + user-registered)."""
    sources = db.list_sources()
    if not sources:
        return "No sources connected."
    default_lines = []
    user_lines = []
    for source_type, identifier, label, is_default in sources:
        entry = f"- {source_type}: {identifier}"
        if label:
            entry += f" ({label})"
        (default_lines if is_default else user_lines).append(entry)
    out = []
    if default_lines:
        out.append("Always-on (defaults):")
        out.extend(default_lines)
    if user_lines:
        if out:
            out.append("")
        out.append("User-registered:")
        out.extend(user_lines)
    else:
        if out:
            out.append("")
        out.append("No user-registered sources yet.")
    return "\n".join(out)
