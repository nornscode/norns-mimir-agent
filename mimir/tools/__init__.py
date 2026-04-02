from mimir.tools.github import read_github_file, search_github
from mimir.tools.google_docs import read_google_doc, search_google_docs
from mimir.tools.memory import remember, search_memory

all_tools = [
    remember,
    search_memory,
    search_github,
    read_github_file,
    search_google_docs,
    read_google_doc,
]
