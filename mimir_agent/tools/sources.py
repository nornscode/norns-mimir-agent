import logging

from norns import tool

from mimir_agent import config, db

logger = logging.getLogger(__name__)

VALID_TYPES = {
    "github_repo": "GitHub repository (owner/repo format)",
    "url": "Any web URL — its content will be fetched and stored in memory",
    "figma_file": "Figma file (file key or full Figma URL) — text content stored in memory",
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


def _ingest_figma_file(identifier: str) -> tuple[bool, str]:
    """Validate access (depth=0), then fetch with depth=2 and store in memory."""
    from mimir_agent.tools.figma import (
        FigmaError,
        extract_file_key,
        fetch_file,
        render_file_summary,
    )
    from mimir_agent.tools.memory import remember

    key = extract_file_key(identifier)
    if not key:
        return False, (
            f"'{identifier}' is not a valid Figma file key or URL. Expected a "
            f"figma.com/file/<key> or figma.com/design/<key> URL, or the bare key."
        )

    try:
        fetch_file(key, depth=0)  # cheap access check
        payload = fetch_file(key, depth=2)
    except FigmaError as e:
        return False, str(e)

    text = render_file_summary(payload)
    remember.handler(key=f"figma:{key}", content=text)
    return True, ""


def _remember_github_repo(identifier: str) -> None:
    """Mirror a connected repo into memory so search_memory can surface it across threads."""
    from mimir_agent.tools.memory import remember

    remember.handler(
        key=f"github_repo:{identifier}",
        content=f"User connected GitHub repo: https://github.com/{identifier}",
    )


@tool(side_effect=True)
def connect_source(source_type: str, identifier: str, label: str = "") -> str:
    """Register a knowledge source. Validates access before adding.

    Types:
    - github_repo: owner/repo format (e.g. "nornscode/norns")
    - url: any web URL (its content is fetched and stored in memory)
    - figma_file: a Figma file URL or bare file key (text content stored in memory)
    """
    if source_type not in VALID_TYPES:
        return (
            f"Unknown source type '{source_type}'. "
            f"Valid types: {', '.join(VALID_TYPES)}"
        )

    identifier = identifier.strip()
    if not identifier:
        return "Identifier is required."

    if source_type == "figma_file":
        from mimir_agent.tools.figma import extract_file_key
        normalized = extract_file_key(identifier)
        if normalized:
            identifier = normalized

    validators = {
        "github_repo": _validate_github_repo,
        "url": _ingest_url,
        "figma_file": _ingest_figma_file,
    }
    ok, err = validators[source_type](identifier)
    if not ok:
        return err

    added = db.add_source(source_type, identifier, label=label or None)
    if source_type == "github_repo":
        _remember_github_repo(identifier)
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
def installation_status() -> str:
    """Return whether this install has been onboarded.

    Returns "onboarded (<n> user sources connected — call list_sources for details)"
    if the user has registered at least one source, otherwise "needs_onboarding".
    Call this once at the start of every new conversation to decide whether to
    run the onboarding flow or proceed normally.
    """
    count = db.user_source_count()
    if count == 0:
        return "needs_onboarding"
    return f"onboarded ({count} user sources connected — call list_sources for details)"


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
