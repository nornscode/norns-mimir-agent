"""Tests for Slack bot message processing — mention resolution, file downloads, link expansion."""

import re
from unittest.mock import MagicMock, patch

from mimir_agent.slack_bot import (
    _download_slack_files,
    _is_text_file,
    _resolve_slack_file_links,
    _resolve_slack_links,
    to_slack_mrkdwn,
)


class TestIsTextFile:
    def test_text_mimetypes(self):
        assert _is_text_file("text/plain", "") is True
        assert _is_text_file("text/html", "") is True
        assert _is_text_file("application/json", "") is True
        assert _is_text_file("application/xml", "") is True

    def test_code_filetypes(self):
        assert _is_text_file("", "python") is True
        assert _is_text_file("", "javascript") is True
        assert _is_text_file("", "markdown") is True
        assert _is_text_file("", "yaml") is True
        assert _is_text_file("", "elixir") is True

    def test_non_text(self):
        assert _is_text_file("image/png", "") is False
        assert _is_text_file("application/pdf", "") is False
        assert _is_text_file("video/mp4", "") is False
        assert _is_text_file("", "mp4") is False


class TestDownloadSlackFiles:
    def test_no_files_returns_empty(self):
        assert _download_slack_files({"text": "hello"}, "xoxb-fake") == ""

    def test_skips_non_text_files(self):
        event = {
            "files": [
                {
                    "name": "photo.png",
                    "mimetype": "image/png",
                    "filetype": "png",
                    "url_private_download": "https://files.slack.com/photo.png",
                }
            ]
        }
        result = _download_slack_files(event, "xoxb-fake")
        assert "not readable as text" in result
        assert "photo.png" in result

    @patch("httpx.get")
    def test_downloads_text_file(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "print('hello')"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        event = {
            "files": [
                {
                    "name": "script.py",
                    "mimetype": "text/x-python",
                    "filetype": "python",
                    "url_private_download": "https://files.slack.com/download/script.py",
                }
            ]
        }
        result = _download_slack_files(event, "xoxb-fake")
        assert "script.py" in result
        assert "print('hello')" in result
        mock_get.assert_called_once()
        # Verify auth header is passed
        call_kwargs = mock_get.call_args
        assert "Bearer xoxb-fake" in str(call_kwargs)

    def test_skips_file_without_download_url(self):
        event = {
            "files": [
                {"name": "snippet.txt", "mimetype": "text/plain", "filetype": "text"}
            ]
        }
        result = _download_slack_files(event, "xoxb-fake")
        assert result == ""


class TestResolveSlackLinks:
    def test_expands_message_link(self):
        client = MagicMock()
        client.conversations_history.return_value = {
            "messages": [{"text": "hello world", "user": "U123"}]
        }
        client.users_info.return_value = {
            "user": {"real_name": "Ryan", "name": "ryan"}
        }

        text = "check this <https://workspace.slack.com/archives/C0ABC/p1234567890123456>"
        result = _resolve_slack_links(text, client)
        assert "Ryan" in result
        assert "hello world" in result

    def test_preserves_non_slack_links(self):
        client = MagicMock()
        text = "visit <https://example.com|Example>"
        result = _resolve_slack_links(text, client)
        assert text == result

    def test_handles_api_failure_gracefully(self):
        client = MagicMock()
        client.conversations_history.side_effect = Exception("API error")

        text = "<https://workspace.slack.com/archives/C0ABC/p1234567890123456>"
        result = _resolve_slack_links(text, client)
        # Should fall back to the URL
        assert "slack.com" in result


class TestResolveSlackFileLinks:
    def test_matches_wrapped_url(self):
        client = MagicMock()
        client.files_info.return_value = {
            "file": {
                "name": "notes.md",
                "mimetype": "text/markdown",
                "filetype": "markdown",
                "content": "# My Notes\n\nSome content here.",
            }
        }

        text = "<https://workspace.slack.com/files/U0ABC/F0DEF/notes.md|notes.md>"
        result = _resolve_slack_file_links(text, client)
        assert "notes.md" in result
        assert "My Notes" in result

    def test_matches_bare_url(self):
        client = MagicMock()
        client.files_info.return_value = {
            "file": {
                "name": "config.yaml",
                "mimetype": "application/x-yaml",
                "filetype": "yaml",
                "content": "key: value",
            }
        }

        text = "https://workspace.slack.com/files/U0ABC/F0DEF/config.yaml"
        result = _resolve_slack_file_links(text, client)
        assert "config.yaml" in result
        assert "key: value" in result

    def test_handles_api_failure(self):
        client = MagicMock()
        client.files_info.side_effect = Exception("not found")

        text = "<https://workspace.slack.com/files/U0ABC/F0DEF/notes.md>"
        result = _resolve_slack_file_links(text, client)
        assert "failed to fetch" in result


class TestSlackMrkdwn:
    def test_converts_markdown_links(self):
        assert "<https://example.com|click>" in to_slack_mrkdwn("[click](https://example.com)")

    def test_converts_headings_to_bold(self):
        result = to_slack_mrkdwn("## My Title")
        assert "My Title" in result

    def test_empty_passthrough(self):
        assert to_slack_mrkdwn("") == ""
        assert to_slack_mrkdwn(None) is None
