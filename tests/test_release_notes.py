"""draft_release_notes — which merged PRs land in the window."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def _pr(number, merged, updated):
    pr = MagicMock()
    pr.number = number
    pr.title = f"PR {number}"
    pr.user.login = "dev"
    pr.labels = []
    pr.merged_at = datetime.fromisoformat(merged).replace(tzinfo=timezone.utc) if merged else None
    pr.updated_at = datetime.fromisoformat(updated).replace(tzinfo=timezone.utc)
    return pr


def _run(pulls, token=""):
    repo = MagicMock()
    repo.get_pulls.return_value = pulls
    repo.get_releases.return_value = []
    client = MagicMock()
    client.get_repo.return_value = repo
    with (
        patch("mimir_agent.tools.github.config") as mock_config,
        patch("mimir_agent.tools.release_notes._get_client", return_value=client),
    ):
        mock_config.GITHUB_TOKEN = token
        from mimir_agent.tools.release_notes import draft_release_notes
        return draft_release_notes.handler("acme/product", "2026-03-01", "2026-03-31")


class TestDraftReleaseNotes:
    def test_works_without_a_token_for_public_repos(self):
        out = _run([_pr(1, "2026-03-10", "2026-03-10")])
        assert "#1: PR 1" in out

    def test_an_old_pr_updated_recently_does_not_hide_the_rest(self):
        """Sorted by update time: an old PR commented on this month comes
        first, and must not end the scan before the in-range ones."""
        out = _run([
            _pr(1, "2025-06-01", "2026-03-20"),
            _pr(2, "2026-03-15", "2026-03-15"),
            _pr(3, None, "2026-03-12"),
            _pr(4, "2026-03-05", "2026-03-05"),
            _pr(5, "2026-02-01", "2026-02-01"),
        ])
        assert "#1:" not in out
        assert "#2: PR 2" in out
        assert "#3:" not in out
        assert "#4: PR 4" in out
        assert "#5:" not in out
        assert "Merged Pull Requests (2)" in out
