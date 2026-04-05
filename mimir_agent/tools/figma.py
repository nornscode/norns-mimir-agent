import logging
import time

import anthropic
import httpx

from mimir_agent import config

logger = logging.getLogger(__name__)

_http_client = None
_file_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300  # 5 minutes


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            base_url="https://api.figma.com/v1",
            headers={"X-Figma-Token": config.FIGMA_ACCESS_TOKEN},
            timeout=30,
        )
    return _http_client


def _get_file(file_key: str) -> dict:
    """Fetch a Figma file tree with caching."""
    now = time.time()
    if file_key in _file_cache:
        data, ts = _file_cache[file_key]
        if now - ts < CACHE_TTL:
            return data

    resp = _get_http_client().get(f"/files/{file_key}", params={"depth": 2})
    resp.raise_for_status()
    data = resp.json()
    _file_cache[file_key] = (data, now)
    return data


def _walk_nodes(node: dict, path: str = "") -> list[dict]:
    """Recursively walk the Figma node tree, collecting nodes with their paths."""
    current_path = f"{path}/{node['name']}" if path else node["name"]
    results = [{"name": node["name"], "type": node.get("type", ""), "id": node["id"], "path": current_path}]
    for child in node.get("children", []):
        results.extend(_walk_nodes(child, current_path))
    return results


from norns import tool


@tool
def search_figma(query: str) -> str:
    """Search Figma design files for frames, components, and layers matching the query."""
    if not config.FIGMA_ACCESS_TOKEN or not config.FIGMA_FILE_KEYS:
        return "Figma is not configured. Set FIGMA_ACCESS_TOKEN and FIGMA_FILE_KEYS."

    query_lower = query.lower()
    matches = []

    for file_key in config.FIGMA_FILE_KEYS:
        try:
            data = _get_file(file_key)
            file_name = data.get("name", file_key)
            document = data.get("document", {})
            nodes = _walk_nodes(document)

            for node in nodes:
                if query_lower in node["name"].lower():
                    matches.append(
                        f"[{node['type']}] {file_name}/{node['path']} "
                        f"(file_key={file_key}, node_id={node['id']})"
                    )
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch Figma file %s: %s", file_key, e)
            continue

    if not matches:
        return f"No Figma nodes found matching '{query}'."

    return "\n".join(matches[:20])


@tool
def render_figma_frame(file_key: str, node_id: str) -> str:
    """Render a Figma frame or component as an image and describe it using vision AI.

    Use the file_key and node_id from search_figma results.
    """
    if not config.FIGMA_ACCESS_TOKEN:
        return "Figma is not configured. Set FIGMA_ACCESS_TOKEN."

    client = _get_http_client()

    resp = client.get(
        f"/images/{file_key}",
        params={"ids": node_id, "format": "png", "scale": 2},
    )
    resp.raise_for_status()
    images = resp.json().get("images", {})

    image_url = images.get(node_id)
    if not image_url:
        return f"Failed to render node {node_id}. It may not be a renderable frame or component."

    img_resp = httpx.get(image_url, timeout=30)
    img_resp.raise_for_status()
    image_data = img_resp.content

    import base64
    image_b64 = base64.b64encode(image_data).decode()

    claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    vision_resp = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Describe this UI mockup in detail. Include the layout structure, "
                        "visual components (buttons, inputs, cards, etc.), text content, "
                        "colors, and overall design pattern. Be specific about positioning "
                        "and hierarchy.",
                    },
                ],
            }
        ],
    )

    description = vision_resp.content[0].text
    return f"[Visual description of Figma node {node_id}]\n\n{description}"
