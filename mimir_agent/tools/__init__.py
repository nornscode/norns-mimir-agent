from mimir_agent.tools.github import (
    list_github_branches,
    list_github_commits,
    list_github_prs,
    read_github_file,
    read_github_pr,
    search_github,
)
from mimir_agent.tools.memory import remember, reset_memory, search_memory
from mimir_agent.tools.release_notes import draft_release_notes
from mimir_agent.tools.sources import connect_source, disconnect_source, list_sources
from mimir_agent.tools.web import read_url

all_tools = [
    remember,
    search_memory,
    reset_memory,
    search_github,
    read_github_file,
    list_github_commits,
    list_github_prs,
    read_github_pr,
    list_github_branches,
    draft_release_notes,
    read_url,
    connect_source,
    disconnect_source,
    list_sources,
]
