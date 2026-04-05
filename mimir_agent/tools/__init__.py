from mimir_agent.tools.figma import render_figma_frame, search_figma
from mimir_agent.tools.github import read_github_file, search_github
from mimir_agent.tools.google_docs import read_google_doc, search_google_docs
from mimir_agent.tools.memory import remember, search_memory
from mimir_agent.tools.release_notes import draft_release_notes

all_tools = [
    remember,
    search_memory,
    search_github,
    read_github_file,
    search_google_docs,
    read_google_doc,
    search_figma,
    render_figma_frame,
    draft_release_notes,
]
