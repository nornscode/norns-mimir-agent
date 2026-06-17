import re

import httpx
from norns import tool

from mimir_agent import config

FIGMA_API_BASE = "https://api.figma.com/v1"
MAX_CHARS = 8000


class FigmaError(Exception):
    """Raised when a Figma API call fails in a way the caller should surface."""


def extract_file_key(identifier: str) -> str | None:
    """Accept a raw Figma file key or a full URL; return the key (or None if unparseable).

    Figma URLs come in two shapes:
      https://www.figma.com/file/<KEY>/<slug>
      https://www.figma.com/design/<KEY>/<slug>
    Bare keys are 22-char alphanumeric strings; we accept anything ≥10 chars without slashes.
    """
    identifier = identifier.strip()
    if not identifier:
        return None

    match = re.search(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)", identifier)
    if match:
        return match.group(1)

    if "/" not in identifier and len(identifier) >= 10 and re.fullmatch(r"[A-Za-z0-9]+", identifier):
        return identifier

    return None


def _request(path: str, params: dict | None = None) -> dict:
    """Call the Figma API. Raises FigmaError with a user-facing message on failure."""
    if not config.FIGMA_TOKEN:
        raise FigmaError(
            "FIGMA_TOKEN is not set. Generate a personal access token at "
            "https://www.figma.com/settings (Personal access tokens) and add "
            "FIGMA_TOKEN to the deploy env."
        )

    try:
        resp = httpx.get(
            f"{FIGMA_API_BASE}{path}",
            headers={"X-Figma-Token": config.FIGMA_TOKEN},
            params=params or {},
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise FigmaError(f"Could not reach Figma: {e}") from e

    if resp.status_code == 404:
        raise FigmaError(
            "Figma file not found or not accessible to this token. Make sure "
            "the file exists and the token's account has been added to it."
        )
    if resp.status_code == 403:
        raise FigmaError(
            "Figma rejected the request (403). The token may lack access to "
            "this file's team, or it may be missing scopes."
        )
    if resp.status_code >= 400:
        raise FigmaError(f"Figma API error {resp.status_code}: {resp.text[:200]}")

    return resp.json()


def fetch_file(file_key: str, depth: int = 2) -> dict:
    """Fetch a Figma file with a depth cap. depth=2 is the default for ingest."""
    return _request(f"/files/{file_key}", params={"depth": depth})


def fetch_node(file_key: str, node_id: str) -> dict:
    """Fetch a specific node subtree (no depth cap — naturally scoped)."""
    return _request(f"/files/{file_key}/nodes", params={"ids": node_id})


def walk_text(document: dict) -> list[str]:
    """Walk a Figma document tree, collecting frame/page names and TEXT node content.

    Yields strings in document order. Caller joins/truncates.
    """
    out: list[str] = []

    def visit(node: dict, depth: int = 0) -> None:
        node_type = node.get("type", "")
        name = node.get("name", "")
        characters = node.get("characters")

        if node_type in ("DOCUMENT", "CANVAS", "FRAME", "GROUP", "SECTION", "COMPONENT", "INSTANCE"):
            if name and node_type != "DOCUMENT":
                out.append(f"{'#' * min(depth, 4)} {name}")
        if node_type == "TEXT" and characters:
            out.append(characters)

        for child in node.get("children", []) or []:
            visit(child, depth + 1)

    visit(document)
    return out


def render_file_summary(file_payload: dict) -> str:
    """Build the text snapshot stored in memory and returned to the LLM."""
    name = file_payload.get("name", "(unnamed)")
    last_modified = file_payload.get("lastModified", "")
    document = file_payload.get("document") or {}

    lines = [f"Figma file: {name}"]
    if last_modified:
        lines.append(f"Last modified: {last_modified}")
    lines.append("")
    lines.extend(walk_text(document))

    text = "\n".join(line for line in lines if line is not None)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n... (truncated, {len(text)} chars total at depth=2)"
    return text


@tool
def read_figma_file(file_key: str) -> str:
    """Fetch a Figma file's text content (frame names + TEXT nodes) at depth=2.

    Use when search_memory's snapshot is stale or you want a fresh read. For
    deeper drilling into a specific frame, use read_figma_node instead.
    """
    key = extract_file_key(file_key) or file_key
    try:
        payload = fetch_file(key, depth=2)
    except FigmaError as e:
        return str(e)
    return render_file_summary(payload)


@tool
def read_figma_node(file_key: str, node_id: str) -> str:
    """Fetch a specific Figma node's full subtree. Use to drill into a frame
    that was truncated by the depth=2 ingest snapshot.

    node_id looks like "1:23" — visible in the Figma URL as ?node-id=1-23
    (replace the dash with a colon).
    """
    key = extract_file_key(file_key) or file_key
    try:
        payload = _request(f"/files/{key}/nodes", params={"ids": node_id})
    except FigmaError as e:
        return str(e)

    nodes = payload.get("nodes") or {}
    if not nodes:
        return f"Node {node_id} not found in file {key}."

    parts: list[str] = []
    for nid, wrapper in nodes.items():
        if wrapper is None:
            parts.append(f"Node {nid}: not found.")
            continue
        document = wrapper.get("document") or {}
        parts.append(f"--- Node {nid}: {document.get('name', '(unnamed)')} ---")
        parts.extend(walk_text(document))

    text = "\n".join(parts)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n... (truncated, {len(text)} chars total)"
    return text
