from github import Github, GithubException

from norns import tool

from mimir_agent import config, db


def _get_client() -> Github:
    # Public repos work unauthenticated (60 req/hr per IP). Use the token
    # when set for private repos or the ~5000 req/hr authenticated limit.
    return Github(config.GITHUB_TOKEN) if config.GITHUB_TOKEN else Github()


def _resolve_repo(repo: str):
    """Resolve a repo string to a PyGithub Repository object. Falls back to connected repos."""
    g = _get_client()
    if repo:
        return g.get_repo(repo)
    repos = db.get_github_repos()
    if repos:
        return g.get_repo(repos[0])
    raise ValueError("No repo specified and none connected.")


@tool
def search_github(query: str, repo: str = "") -> str:
    """Search code and issues in connected GitHub repos. Optionally filter to a specific repo (owner/repo format)."""
    try:
        g = _get_client()
    except ValueError as e:
        return str(e)

    repos = [repo] if repo else db.get_github_repos()
    if not repos:
        return "No GitHub repos connected. Use connect_source to add one."

    results = []

    for repo_name in repos:
        try:
            # Search code
            code_results = g.search_code(query, repo=repo_name)
            for item in code_results[:5]:
                results.append(f"[code] {repo_name}/{item.path}")

            # Search issues
            issue_results = g.search_issues(query, repo=repo_name)
            for item in issue_results[:5]:
                state = item.state
                results.append(f"[issue #{item.number} {state}] {repo_name}: {item.title}")

        except (GithubException, IndexError) as e:
            msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
            results.append(f"[error] {repo_name}: {msg}")

    if not results:
        return f"No results found for '{query}' in {', '.join(repos)}."

    return "\n".join(results[:20])


# Per-call ceiling. The old limit was 8000 bytes with no way to ask for the
# rest, which quietly read the first quarter of a 30KB decision log and
# reported it as the file. Mirrors sleipnir's read_file, which this is now
# shaped like, so the two tool surfaces behave the same way.
MAX_READ_CHARS = 40_000


@tool
def read_github_file(repo: str, path: str, offset: int = 1, limit: int = 0) -> str:
    """Read a file from a GitHub repo, or list a directory. Use owner/repo
    format for the repo parameter.

    offset is the 1-based first line to return and limit the number of lines
    (0 for all). A file longer than the output ceiling is cut, and the last
    line of the output says which lines you got and the offset to continue
    from — so a long file takes several calls rather than silently arriving
    as its first page. Chronological files like a decision log put the
    recent material at the *end*: read the tail, not the head.
    """
    try:
        g = _get_client()
    except ValueError as e:
        return str(e)

    try:
        repository = g.get_repo(repo)
        content = repository.get_contents(path)
    except GithubException as e:
        return f"Error reading {repo}/{path}: {e.data.get('message', str(e))}"

    if isinstance(content, list):
        entries = [f"{'dir' if c.type == 'dir' else 'file'}: {c.path}" for c in content]
        return f"Directory listing for {repo}/{path}:\n" + "\n".join(entries)

    try:
        text = content.decoded_content.decode("utf-8")
    except UnicodeDecodeError:
        return f"{repo}/{path} is not UTF-8 text ({content.size} bytes)."

    return _page(text, offset, limit)


def _page(text: str, offset: int, limit: int) -> str:
    """One page of a file, with a footer saying where it sits in the whole.

    A negative offset counts back from the end, so offset=-80 is the last 80
    lines — the cheap way to reach the recent end of a file that grows by
    appending.
    """
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return "(empty file)"

    if offset < 0:
        start = max(total + offset, 0)
    else:
        start = max(offset, 1) - 1
    if start >= total:
        return f"(offset {offset} is past the end; the file has {total} lines)"
    end = total if limit <= 0 else min(total, start + limit)

    out: list[str] = []
    used = 0
    for i, line in enumerate(lines[start:end]):
        used += len(line) + 1
        if used > MAX_READ_CHARS:
            end = start + i
            break
        out.append(line)

    body = "\n".join(out)
    if start > 0 or end < total:
        footer = f"[lines {start + 1}-{end} of {total}"
        if end < total:
            footer += f"; continue with offset={end + 1}"
        if start > 0:
            footer += f"; earlier lines at offset=1"
        footer += "]"
        body = f"{body}\n{footer}" if body else footer
    return body or "(empty file)"


@tool
def list_github_commits(repo: str, branch: str = "", since: str = "", limit: int = 20) -> str:
    """List recent commits for a GitHub repo. Optionally filter by branch and since date (YYYY-MM-DD)."""
    try:
        repository = _resolve_repo(repo)
    except (ValueError, GithubException) as e:
        return str(e)

    kwargs = {}
    if branch:
        kwargs["sha"] = branch
    if since:
        from datetime import datetime, timezone
        try:
            kwargs["since"] = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return f"Invalid date format: {since}. Use YYYY-MM-DD."

    try:
        commits = repository.get_commits(**kwargs)
        lines = []
        for c in commits[:limit]:
            date = c.commit.author.date.strftime("%Y-%m-%d")
            author = c.commit.author.name
            msg = c.commit.message.split("\n")[0]
            lines.append(f"{c.sha[:7]} {date} ({author}) {msg}")
        return "\n".join(lines) if lines else "No commits found."
    except (GithubException, IndexError) as e:
        msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
        return f"Error: {msg}"


@tool
def list_github_prs(repo: str, state: str = "open", limit: int = 10) -> str:
    """List pull requests for a GitHub repo. State can be 'open', 'closed', or 'all'."""
    try:
        repository = _resolve_repo(repo)
    except (ValueError, GithubException) as e:
        return str(e)

    try:
        pulls = repository.get_pulls(state=state, sort="updated", direction="desc")
        lines = []
        for pr in pulls[:limit]:
            labels = ", ".join(l.name for l in pr.labels) if pr.labels else ""
            label_str = f" [{labels}]" if labels else ""
            lines.append(f"#{pr.number} ({pr.state}) {pr.title} — @{pr.user.login}{label_str}")
        return "\n".join(lines) if lines else f"No {state} PRs found."
    except (GithubException, IndexError) as e:
        msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
        return f"Error: {msg}"


@tool
def read_github_pr(repo: str, pr_number: int) -> str:
    """Read details of a specific pull request including description and comments."""
    try:
        repository = _resolve_repo(repo)
    except (ValueError, GithubException) as e:
        return str(e)

    try:
        pr = repository.get_pull(pr_number)
        out = f"#{pr.number}: {pr.title}\n"
        out += f"State: {pr.state} | Author: @{pr.user.login} | Branch: {pr.head.ref} → {pr.base.ref}\n"
        out += f"Created: {pr.created_at.strftime('%Y-%m-%d')} | Updated: {pr.updated_at.strftime('%Y-%m-%d')}\n"
        if pr.merged_at:
            out += f"Merged: {pr.merged_at.strftime('%Y-%m-%d')}\n"
        out += f"\n{pr.body or '(no description)'}\n"

        comments = list(pr.get_issue_comments()[:10])
        if comments:
            out += f"\n--- Comments ({pr.comments} total) ---\n"
            for c in comments:
                body = c.body[:300] if len(c.body) > 300 else c.body
                out += f"\n@{c.user.login} ({c.created_at.strftime('%Y-%m-%d')}): {body}\n"

        if len(out) > 8000:
            out = out[:8000] + "\n\n... (truncated)"
        return out
    except GithubException as e:
        return f"Error: {e.data.get('message', str(e))}"


@tool
def list_github_branches(repo: str) -> str:
    """List branches for a GitHub repo."""
    try:
        repository = _resolve_repo(repo)
    except (ValueError, GithubException) as e:
        return str(e)

    try:
        branches = repository.get_branches()
        lines = []
        for b in branches[:30]:
            default = " (default)" if b.name == repository.default_branch else ""
            lines.append(f"{b.name}{default}")
        return "\n".join(lines) if lines else "No branches found."
    except (GithubException, IndexError) as e:
        msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
        return f"Error: {msg}"
