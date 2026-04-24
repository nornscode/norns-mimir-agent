"""Tests for default sources, onboarding trigger, and connect_source validation."""

from unittest.mock import MagicMock, patch


class TestOnboardingTrigger:
    def test_empty_returns_onboarding_prompt(self):
        with patch("mimir_agent.db.user_source_count", return_value=0):
            from mimir_agent.worker import _build_onboarding_section, ONBOARDING_PROMPT
            assert _build_onboarding_section() == ONBOARDING_PROMPT

    def test_user_sources_silence_onboarding(self):
        with patch("mimir_agent.db.user_source_count", return_value=1):
            from mimir_agent.worker import _build_onboarding_section
            assert _build_onboarding_section() == ""


class TestSourcesSection:
    def test_separates_defaults_from_user(self):
        fake_rows = [
            ("github_repo", "nornscode/norns", "Norns durable runtime", True),
            ("github_repo", "acme/product", None, False),
        ]
        with patch("mimir_agent.db.list_sources", return_value=fake_rows):
            from mimir_agent.worker import _build_sources_section
            out = _build_sources_section()

        assert "Always-on" in out
        assert "User-registered" in out
        assert "nornscode/norns" in out
        assert "acme/product" in out


class TestConnectSource:
    def test_rejects_unknown_type(self):
        from mimir_agent.tools.sources import connect_source
        result = connect_source.handler("slack_channel", "C123")
        assert "Unknown source type" in result

    def test_rejects_empty_identifier(self):
        from mimir_agent.tools.sources import connect_source
        result = connect_source.handler("github_repo", "  ")
        assert "required" in result.lower()

    def test_bad_github_format_fails_validation(self):
        from mimir_agent.tools.sources import connect_source
        result = connect_source.handler("github_repo", "just-a-name")
        assert "owner/repo" in result

    def test_github_validates_then_adds(self):
        fake_repo = MagicMock(full_name="acme/product")
        fake_client = MagicMock()
        fake_client.get_repo.return_value = fake_repo

        with (
            patch("mimir_agent.tools.sources.config") as mock_config,
            patch("mimir_agent.tools.sources.db") as mock_db,
            patch("github.Github", return_value=fake_client),
        ):
            mock_config.GITHUB_TOKEN = "ghp_fake"
            mock_db.add_source.return_value = True
            from mimir_agent.tools.sources import connect_source
            result = connect_source.handler("github_repo", "acme/product")

        mock_db.add_source.assert_called_once()
        assert "Connected" in result

    def test_url_ingest_stores_in_memory(self):
        with (
            patch("mimir_agent.tools.web._fetch_one", return_value="hello world"),
            patch("mimir_agent.tools.memory.remember") as mock_remember,
            patch("mimir_agent.tools.sources.db") as mock_db,
        ):
            mock_db.add_source.return_value = True
            from mimir_agent.tools.sources import connect_source
            result = connect_source.handler("url", "https://example.com/docs")

        mock_remember.handler.assert_called_once()
        call_kwargs = mock_remember.handler.call_args
        assert "example.com" in call_kwargs.kwargs["key"]
        assert "Connected" in result


class TestListSourcesTool:
    def test_groups_defaults_and_user(self):
        fake_rows = [
            ("github_repo", "nornscode/norns", "Norns", True),
            ("github_repo", "acme/product", None, False),
            ("url", "https://example.com", None, False),
        ]
        with patch("mimir_agent.tools.sources.db") as mock_db:
            mock_db.list_sources.return_value = fake_rows
            from mimir_agent.tools.sources import list_sources
            out = list_sources.handler()
        assert "Always-on" in out
        assert "nornscode/norns" in out
        assert "User-registered" in out
        assert "acme/product" in out
        assert "https://example.com" in out

    def test_only_defaults_shows_no_user_message(self):
        fake_rows = [("github_repo", "nornscode/norns", None, True)]
        with patch("mimir_agent.tools.sources.db") as mock_db:
            mock_db.list_sources.return_value = fake_rows
            from mimir_agent.tools.sources import list_sources
            out = list_sources.handler()
        assert "No user-registered" in out


class TestDefaultSeeding:
    def test_seed_uses_is_default_true(self):
        with (
            patch("mimir_agent.db.config") as mock_config,
            patch("mimir_agent.db.add_source") as mock_add,
        ):
            mock_config.DEFAULT_SOURCES = [
                ("github_repo", "nornscode/norns", "Norns"),
            ]
            from mimir_agent.db import _seed_default_sources
            _seed_default_sources()

        mock_add.assert_called_once_with(
            "github_repo", "nornscode/norns", label="Norns", is_default=True
        )
