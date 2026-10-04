"""Issue tools.

`search_github` can find an issue if you already know what to search for.
The steward's problem is the opposite one: it needs the whole open list
across several repos before it knows what matters. These enumerate.
"""

from github import GithubException

from norns import tool

from mimir_agent.tools.github import _get_client, _resolve_repo


def _repo_list(repo: str) -> list[str]:
    from mimir_agent import db

    if repo:
        return [r.strip() for r in repo.split(",") if r.strip()]
    return db.get_github_repos()


@tool
def list_github_issues(repo: str = "", state: str = "open", limit: int = 20) -> str:
    """List issues for one or more GitHub repos, newest activity first.

    Pass a single repo as owner/repo, several as a comma-separated list, or
    leave it empty to cover every connected repo. State is 'open', 'closed',
    or 'all'. Pull requests are excluded — use list_github_prs for those.
    """
    repos = _repo_list(repo)
    if not repos:
        return "No GitHub repos connected. Use connect_source to add one."

    try:
        g = _get_client()
    except ValueError as e:
        return str(e)

    lines: list[str] = []
    for name in repos:
        try:
            issues = g.get_repo(name).get_issues(state=state, sort="updated", direction="desc")
            found = 0
            for issue in issues:
                # GitHub's issues endpoint returns PRs too; they are not issues.
                if issue.pull_request is not None:
                    continue
                labels = ", ".join(l.name for l in issue.labels)
                suffix = f" [{labels}]" if labels else ""
                age = issue.updated_at.date().isoformat() if issue.updated_at else "?"
                lines.append(
                    f"{name}#{issue.number} ({issue.state}, updated {age}) {issue.title}{suffix}"
                )
                found += 1
                if found >= limit:
                    break
            if found == 0:
                lines.append(f"{name}: no {state} issues")
        except (GithubException, IndexError) as e:
            msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
            lines.append(f"[error] {name}: {msg}")

    return "\n".join(lines)


@tool
def read_github_issue(repo: str, issue_number: int) -> str:
    """Read one issue in full: body, labels, and its comments."""
    try:
        repository = _resolve_repo(repo)
    except (ValueError, GithubException) as e:
        return str(e)

    try:
        issue = repository.get_issue(issue_number)
    except (GithubException, IndexError) as e:
        msg = e.data.get("message", str(e)) if isinstance(e, GithubException) else str(e)
        return f"Error: {msg}"

    labels = ", ".join(l.name for l in issue.labels) or "none"
    parts = [
        f"#{issue.number} ({issue.state}) {issue.title}",
        f"opened by @{issue.user.login if issue.user else 'unknown'}, labels: {labels}",
        "",
        issue.body or "(no description)",
    ]

    try:
        comments = list(issue.get_comments()[:10])
    except (GithubException, IndexError):
        comments = []

    for c in comments:
        author = c.user.login if c.user else "unknown"
        parts.append(f"\n--- comment by @{author} ---\n{c.body or ''}")

    return "\n".join(parts)
