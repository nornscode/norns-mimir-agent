import logging
from datetime import datetime, timezone

from norns import tool

from mimir_agent import config

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from github import Github
        _client = Github(config.GITHUB_TOKEN)
    return _client


@tool
def draft_release_notes(repo: str, since: str, until: str = "") -> str:
    """Fetch merged pull requests and releases from any GitHub repo for a date range.
    YOU MUST USE THIS TOOL when a user asks for release notes. This works with any
    public GitHub repo — not just the repos in GITHUB_REPOS config.

    Args:
        repo: Repository in owner/repo format (e.g. "amackera/norns")
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format (defaults to today)
    """
    if not config.GITHUB_TOKEN:
        return "GitHub is not configured. Set GITHUB_TOKEN."

    try:
        since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return f"Invalid date format for 'since': {since}. Use YYYY-MM-DD."

    if until:
        try:
            until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return f"Invalid date format for 'until': {until}. Use YYYY-MM-DD."
    else:
        until_dt = datetime.now(timezone.utc)

    gh = _get_client()
    try:
        repository = gh.get_repo(repo)
    except Exception as e:
        return f"Could not access repo '{repo}': {e}"

    # Gather merged PRs
    pr_lines = []
    pulls = repository.get_pulls(state="closed", sort="updated", direction="desc")
    for pr in pulls:
        if pr.merged_at is None:
            continue
        merged = pr.merged_at.replace(tzinfo=timezone.utc)
        if merged < since_dt:
            break
        if merged > until_dt:
            continue
        labels = ", ".join(l.name for l in pr.labels) if pr.labels else "none"
        pr_lines.append(
            f"- #{pr.number}: {pr.title} (by @{pr.user.login}, merged {merged.strftime('%Y-%m-%d')}) [labels: {labels}]"
        )
        if len(pr_lines) >= 100:
            break

    # Gather releases
    release_lines = []
    for release in repository.get_releases():
        if release.published_at is None:
            continue
        pub = release.published_at.replace(tzinfo=timezone.utc)
        if pub < since_dt:
            break
        if pub > until_dt:
            continue
        body_snippet = (release.body or "")[:500]
        release_lines.append(
            f"- {release.tag_name}: {release.title} ({pub.strftime('%Y-%m-%d')})\n  {body_snippet}"
        )

    # Build output
    output = f"## Release Data for {repo} ({since} to {until or 'today'})\n\n"
    output += f"### Merged Pull Requests ({len(pr_lines)})\n"
    output += "\n".join(pr_lines) if pr_lines else "No merged PRs in this period."
    output += f"\n\n### Releases ({len(release_lines)})\n"
    output += "\n".join(release_lines) if release_lines else "No releases in this period."

    return output[:8000]
