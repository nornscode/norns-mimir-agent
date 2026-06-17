from norns import tool

from mimir_agent import db


@tool(side_effect=True)
def set_channel_project(channel_id: str, project_name: str) -> str:
    """Associate a Slack channel with a project. All memories and sources
    created in that channel will default to this project.

    Use the channel ID (e.g. "C01ABC23DEF") and a short project name
    (e.g. "missive", "norns").
    """
    project_name = project_name.strip().lower()
    if not project_name:
        return "Project name is required."
    db.set_channel_project(channel_id, project_name)
    return f"Channel {channel_id} is now mapped to project '{project_name}'."


@tool
def list_projects() -> str:
    """List all registered projects and their associated channels."""
    projects = db.list_projects()
    if not projects:
        return "No projects registered yet. Use set_channel_project to create one."
    lines = []
    for name, channel_id in projects:
        if channel_id:
            lines.append(f"- {name} (channel: {channel_id})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)
