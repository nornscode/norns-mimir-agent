"""Tests for multi-project support — tools and DB scoping."""

from unittest.mock import patch, MagicMock


class TestSetChannelProject:
    def test_sets_channel_mapping(self):
        with patch("mimir_agent.tools.projects.db") as mock_db:
            from mimir_agent.tools.projects import set_channel_project
            result = set_channel_project.handler("C0ABC123", "missive")

        mock_db.set_channel_project.assert_called_once_with("C0ABC123", "missive")
        assert "missive" in result

    def test_normalizes_name(self):
        with patch("mimir_agent.tools.projects.db") as mock_db:
            from mimir_agent.tools.projects import set_channel_project
            result = set_channel_project.handler("C0ABC123", "  Missive  ")

        mock_db.set_channel_project.assert_called_once_with("C0ABC123", "missive")

    def test_rejects_empty_name(self):
        with patch("mimir_agent.tools.projects.db") as mock_db:
            from mimir_agent.tools.projects import set_channel_project
            result = set_channel_project.handler("C0ABC123", "   ")

        assert "required" in result
        mock_db.set_channel_project.assert_not_called()


class TestListProjects:
    def test_lists_projects(self):
        with patch("mimir_agent.tools.projects.db") as mock_db:
            mock_db.list_projects.return_value = [
                ("missive", "C0ABC123"),
                ("norns", "C0DEF456"),
            ]
            from mimir_agent.tools.projects import list_projects
            result = list_projects.handler()

        assert "missive" in result
        assert "norns" in result
        assert "C0ABC123" in result

    def test_empty_returns_message(self):
        with patch("mimir_agent.tools.projects.db") as mock_db:
            mock_db.list_projects.return_value = []
            from mimir_agent.tools.projects import list_projects
            result = list_projects.handler()

        assert "No projects" in result


class TestMemoryProjectScoping:
    def test_remember_passes_project(self):
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=[0.1, 0.2]),
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            from mimir_agent.tools.memory import remember
            result = remember.handler("key1", "some fact", project="missive")

        mock_db.upsert_memory.assert_called_once()
        assert mock_db.upsert_memory.call_args.kwargs["project"] == "missive"
        assert "missive" in result

    def test_search_memory_passes_project(self):
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=[0.1, 0.2]),
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            mock_db.search_memories.return_value = [
                ("key1", "fact", 0.9, "missive"),
            ]
            from mimir_agent.tools.memory import search_memory
            result = search_memory.handler("query", project="missive")

        mock_db.search_memories.assert_called_once()
        assert mock_db.search_memories.call_args.kwargs["project"] == "missive"
        assert "missive" in result

    def test_search_memory_all_projects(self):
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=[0.1, 0.2]),
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            mock_db.search_memories.return_value = [
                ("k1", "fact1", 0.9, "missive"),
                ("k2", "fact2", 0.8, "norns"),
            ]
            from mimir_agent.tools.memory import search_memory
            result = search_memory.handler("query", project="all")

        # project="all" should pass None to search all
        assert mock_db.search_memories.call_args.kwargs["project"] is None


class TestSourcesProjectScoping:
    def test_connect_source_passes_project(self):
        with (
            patch("mimir_agent.tools.sources.config") as mock_config,
            patch("mimir_agent.tools.sources.db") as mock_db,
            patch("mimir_agent.tools.memory.remember") as mock_remember,
            patch("github.Github") as mock_github_cls,
        ):
            mock_config.GITHUB_TOKEN = "ghp_fake"
            fake_repo = MagicMock()
            fake_repo.full_name = "acme/product"
            mock_github_cls.return_value.get_repo.return_value = fake_repo
            mock_db.add_source.return_value = True

            from mimir_agent.tools.sources import connect_source
            result = connect_source.handler("github_repo", "acme/product", project="missive")

        mock_db.add_source.assert_called_once()
        assert mock_db.add_source.call_args.kwargs.get("project") == "missive" or \
               mock_db.add_source.call_args[0][-1] == "missive" or \
               "missive" in str(mock_db.add_source.call_args)

    def test_list_sources_passes_project(self):
        with patch("mimir_agent.tools.sources.db") as mock_db:
            mock_db.list_sources.return_value = [
                ("github_repo", "acme/product", None, False, "missive"),
            ]
            from mimir_agent.tools.sources import list_sources
            result = list_sources.handler(project="missive")

        mock_db.list_sources.assert_called_once_with(project="missive")


class TestReadUrlSlackRejection:
    def test_rejects_slack_urls(self):
        from mimir_agent.tools.web import read_url
        result = read_url.handler(["https://norns-workspace.slack.com/files/U123/F456/file.md"])
        assert "Slack URLs require authentication" in result

    def test_allows_non_slack_urls(self):
        with patch("mimir_agent.tools.web._fetch_one", return_value="page content"):
            from mimir_agent.tools.web import read_url
            result = read_url.handler(["https://example.com"])
        assert "page content" in result
