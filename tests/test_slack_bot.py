"""Tests for Slack bot message processing — mention resolution, file downloads, link expansion."""

import re
from unittest.mock import MagicMock, patch

from mimir_agent.slack_bot import (
    _download_slack_files,
    _fetch_thread_context,
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

    def test_reports_file_without_download_url(self):
        event = {
            "files": [
                {"name": "snippet.txt", "mimetype": "text/plain", "filetype": "text"}
            ]
        }
        result = _download_slack_files(event, "xoxb-fake")
        assert "no download URL" in result
        assert "snippet.txt" in result


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


class TestFetchThreadContext:
    def test_returns_prior_messages_with_names(self):
        client = MagicMock()
        client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1.0", "user": "U1", "text": "First message"},
                {"ts": "2.0", "user": "U2", "text": "Second message"},
                {"ts": "3.0", "user": "U1", "text": "<@UBOT> review this"},
            ]
        }
        client.users_info.side_effect = lambda user: {
            "user": {"real_name": "Ryan" if user == "U1" else "Sam", "name": user}
        }

        result = _fetch_thread_context("C0", "1.0", "3.0", client, "UBOT")
        assert "Ryan: First message" in result
        assert "Sam: Second message" in result
        # Current message excluded
        assert "review this" not in result

    def test_skips_when_bot_already_in_thread(self):
        client = MagicMock()
        client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1.0", "user": "U1", "text": "Hello"},
                {"ts": "2.0", "user": "UBOT", "text": "Previous bot reply"},
                {"ts": "3.0", "user": "U1", "text": "@bot another question"},
            ]
        }
        result = _fetch_thread_context("C0", "1.0", "3.0", client, "UBOT")
        assert result is None

    def test_returns_none_when_no_prior_messages(self):
        client = MagicMock()
        client.conversations_replies.return_value = {
            "messages": [{"ts": "1.0", "user": "U1", "text": "only message"}]
        }
        result = _fetch_thread_context("C0", "1.0", "1.0", client, "UBOT")
        assert result is None

    def test_skips_bot_messages_and_subtypes(self):
        client = MagicMock()
        client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1.0", "user": "U1", "text": "real message"},
                {"ts": "2.0", "user": "U2", "text": "channel topic", "subtype": "channel_topic"},
                {"ts": "3.0", "bot_id": "B999", "text": "some app post"},
                {"ts": "4.0", "user": "U1", "text": "@mimir help"},
            ]
        }
        client.users_info.return_value = {"user": {"real_name": "Ryan", "name": "ryan"}}
        result = _fetch_thread_context("C0", "1.0", "4.0", client, "UBOT")
        assert "real message" in result
        assert "channel topic" not in result
        assert "some app post" not in result

    def test_handles_api_failure(self):
        client = MagicMock()
        client.conversations_replies.side_effect = Exception("api down")
        assert _fetch_thread_context("C0", "1.0", "2.0", client, "UBOT") is None


class TestSlackMrkdwn:
    def test_converts_markdown_links(self):
        assert "<https://example.com|click>" in to_slack_mrkdwn("[click](https://example.com)")

    def test_converts_headings_to_bold(self):
        result = to_slack_mrkdwn("## My Title")
        assert "My Title" in result

    def test_empty_passthrough(self):
        assert to_slack_mrkdwn("") == ""
        assert to_slack_mrkdwn(None) is None
