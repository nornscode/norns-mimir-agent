import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from github import GithubException


class TestSearchGithub:
    def test_works_without_token(self, monkeypatch):
        """Unauthenticated access is allowed for public repos."""
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "")

        mock_github = MagicMock()
        mock_github.search_code.return_value = []
        mock_github.search_issues.return_value = []

        with (
            patch("mimir_agent.tools.github.Github", return_value=mock_github) as mock_class,
            patch("mimir_agent.tools.github.db") as mock_db,
        ):
            mock_db.get_github_repos.return_value = ["nornscode/norns"]
            from mimir_agent.tools.github import search_github
            result = search_github.handler("test query")

        # Github was instantiated without a token argument
        mock_class.assert_called_with()
        assert "not configured" not in result

    def test_no_repos(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")
        with (
            patch("mimir_agent.tools.github.Github"),
            patch("mimir_agent.tools.github.db") as mock_db,
        ):
            mock_db.get_github_repos.return_value = []
            from mimir_agent.tools.github import search_github
            result = search_github.handler("test", repo="")
            assert "No GitHub repos connected" in result

    def test_search_returns_results(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_code_item = MagicMock()
        mock_code_item.path = "src/main.py"

        mock_issue = MagicMock()
        mock_issue.number = 42
        mock_issue.state = "open"
        mock_issue.title = "Bug report"

        mock_github = MagicMock()
        mock_github.search_code.return_value = [mock_code_item]
        mock_github.search_issues.return_value = [mock_issue]

        with (
            patch("mimir_agent.tools.github.Github", return_value=mock_github),
            patch("mimir_agent.tools.github.db") as mock_db,
        ):
            mock_db.get_github_repos.return_value = ["owner/repo"]
            from mimir_agent.tools.github import search_github
            result = search_github.handler("test query")

        assert "src/main.py" in result
        assert "#42" in result
        assert "Bug report" in result

    def test_handles_github_exception(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_github = MagicMock()
        mock_github.search_code.side_effect = GithubException(403, {"message": "rate limited"}, {})

        with (
            patch("mimir_agent.tools.github.Github", return_value=mock_github),
            patch("mimir_agent.tools.github.db") as mock_db,
        ):
            mock_db.get_github_repos.return_value = ["owner/repo"]
            from mimir_agent.tools.github import search_github
            result = search_github.handler("test query")

        assert "error" in result.lower()

    def test_handles_index_error(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_github = MagicMock()
        mock_github.search_code.side_effect = IndexError("list index out of range")

        with (
            patch("mimir_agent.tools.github.Github", return_value=mock_github),
            patch("mimir_agent.tools.github.db") as mock_db,
        ):
            mock_db.get_github_repos.return_value = ["owner/repo"]
            from mimir_agent.tools.github import search_github
            result = search_github.handler("test query")

        assert "error" in result.lower()


class TestReadGithubFile:
    def test_reads_file(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_content = MagicMock()
        mock_content.decoded_content = b"file contents here"
        type(mock_content).type = PropertyMock(return_value="file")

        mock_repo = MagicMock()
        mock_repo.get_contents.return_value = mock_content

        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        with patch("mimir_agent.tools.github.Github", return_value=mock_github):
            from mimir_agent.tools.github import read_github_file
            result = read_github_file.handler("owner/repo", "README.md")

        assert "file contents here" in result

    def test_lists_directory(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        entry1 = MagicMock()
        entry1.type = "file"
        entry1.path = "src/main.py"
        entry2 = MagicMock()
        entry2.type = "dir"
        entry2.path = "src/utils"

        mock_repo = MagicMock()
        mock_repo.get_contents.return_value = [entry1, entry2]

        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        with patch("mimir_agent.tools.github.Github", return_value=mock_github):
            from mimir_agent.tools.github import read_github_file
            result = read_github_file.handler("owner/repo", "src")

        assert "src/main.py" in result
        assert "src/utils" in result
        assert "dir:" in result

    def _read(self, monkeypatch, body: bytes, **kwargs) -> str:
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_content = MagicMock()
        mock_content.decoded_content = body
        type(mock_content).type = PropertyMock(return_value="file")

        mock_repo = MagicMock()
        mock_repo.get_contents.return_value = mock_content

        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        with patch("mimir_agent.tools.github.Github", return_value=mock_github):
            from mimir_agent.tools.github import read_github_file
            return read_github_file.handler("owner/repo", "big.txt", **kwargs)

    def test_a_file_over_the_ceiling_says_where_to_continue(self, monkeypatch):
        """It used to cut at 8KB and say only "truncated", with no way to ask
        for the rest — so a 30KB decision log arrived as its first quarter."""
        body = ("\n".join("x" * 500 for _ in range(200))).encode()
        result = self._read(monkeypatch, body)

        assert "continue with offset=" in result.splitlines()[-1]

    def test_a_file_the_old_limit_would_have_cut_now_arrives_whole(self, monkeypatch):
        body = ("\n".join(f"line {i}" for i in range(1, 1500))).encode()
        result = self._read(monkeypatch, body)

        assert "line 1499" in result
        assert not result.splitlines()[-1].startswith("[")

    def test_the_tail_is_reachable_in_one_call(self, monkeypatch):
        body = ("\n".join(f"line {i}" for i in range(1, 401))).encode()
        result = self._read(monkeypatch, body, offset=-3)

        body_lines = [l for l in result.splitlines() if not l.startswith("[")]
        assert body_lines == ["line 398", "line 399", "line 400"]

    def test_non_utf8_content_is_reported_not_raised(self, monkeypatch):
        result = self._read(monkeypatch, b"\xff\xfe\x00binary")
        assert "not UTF-8" in result


class TestListGithubCommits:
    def test_lists_commits(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_commit = MagicMock()
        mock_commit.sha = "abc1234567890"
        mock_commit.commit.author.date = datetime(2026, 4, 15, tzinfo=timezone.utc)
        mock_commit.commit.author.name = "Dev"
        mock_commit.commit.message = "Fix bug\n\nDetails here"

        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]

        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        with patch("mimir_agent.tools.github.Github", return_value=mock_github):
            from mimir_agent.tools.github import list_github_commits
            result = list_github_commits.handler("owner/repo")

        assert "abc1234" in result
        assert "Fix bug" in result
        assert "Dev" in result

    def test_invalid_date(self, monkeypatch):
        monkeypatch.setattr("mimir_agent.config.GITHUB_TOKEN", "fake-token")

        mock_repo = MagicMock()
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        with patch("mimir_agent.tools.github.Github", return_value=mock_github):
            from mimir_agent.tools.github import list_github_commits
            result = list_github_commits.handler("owner/repo", since="not-a-date")

        assert "Invalid date" in result


# --- Paging a file ---------------------------------------------------------
#
# read_github_file used to cut at 8000 bytes with no way to ask for the rest,
# which read the first quarter of a 30KB chronological decision log and
# reported it as the file. These pin the paging that replaced it.


class TestPage:
    def _text(self, n: int) -> str:
        return "\n".join(f"line {i}" for i in range(1, n + 1))

    def test_a_short_file_comes_back_whole_with_no_footer(self):
        from mimir_agent.tools.github import _page

        out = _page(self._text(5), 1, 0)
        assert out == "line 1\nline 2\nline 3\nline 4\nline 5"

    def test_a_limit_says_where_to_continue(self):
        from mimir_agent.tools.github import _page

        out = _page(self._text(100), 1, 10)
        assert out.startswith("line 1\n")
        assert out.splitlines()[-1] == "[lines 1-10 of 100; continue with offset=11]"

    def test_continuing_from_the_offset_returns_the_next_page(self):
        from mimir_agent.tools.github import _page

        out = _page(self._text(100), 11, 10)
        assert out.splitlines()[0] == "line 11"
        assert "[lines 11-20 of 100; continue with offset=21" in out

    def test_a_negative_offset_reads_the_tail(self):
        """The decision log appends, so the recent material is at the end."""
        from mimir_agent.tools.github import _page

        out = _page(self._text(100), -3, 0)
        body = [l for l in out.splitlines() if not l.startswith("[")]
        assert body == ["line 98", "line 99", "line 100"]
        assert "earlier lines at offset=1" in out.splitlines()[-1]

    def test_a_negative_offset_larger_than_the_file_starts_at_the_top(self):
        from mimir_agent.tools.github import _page

        out = _page(self._text(3), -99, 0)
        assert out == "line 1\nline 2\nline 3"

    def test_an_offset_past_the_end_says_so(self):
        from mimir_agent.tools.github import _page

        assert _page(self._text(10), 99, 0) == "(offset 99 is past the end; the file has 10 lines)"

    def test_an_empty_file_is_not_an_error(self):
        from mimir_agent.tools.github import _page

        assert _page("", 1, 0) == "(empty file)"

    def test_the_output_ceiling_is_enforced_and_reported(self):
        from mimir_agent.tools import github

        # Lines long enough that the ceiling bites before the line count does.
        text = "\n".join("x" * 1000 for _ in range(200))
        out = github._page(text, 1, 0)
        assert len(out) <= github.MAX_READ_CHARS + 200
        assert "continue with offset=" in out.splitlines()[-1]

    def test_a_whole_decision_log_sized_file_fits_in_one_call(self):
        """The case that motivated this: 30KB arriving complete, not quartered."""
        from mimir_agent.tools import github

        text = self._text(1200)  # ~10KB
        out = github._page(text, 1, 0)
        assert "line 1200" in out
        assert not out.splitlines()[-1].startswith("[")
