from unittest.mock import MagicMock, patch

import httpx
import pytest

from mimir_agent.tools import figma


class TestExtractFileKey:
    def test_file_url(self):
        assert figma.extract_file_key(
            "https://www.figma.com/file/aBcD1234EFgh5678/My-Doc"
        ) == "aBcD1234EFgh5678"

    def test_design_url(self):
        assert figma.extract_file_key(
            "https://www.figma.com/design/aBcD1234EFgh5678/My-Doc?node-id=1-2"
        ) == "aBcD1234EFgh5678"

    def test_bare_key(self):
        assert figma.extract_file_key("aBcD1234EFgh5678") == "aBcD1234EFgh5678"

    def test_short_garbage_rejected(self):
        assert figma.extract_file_key("nope") is None

    def test_empty(self):
        assert figma.extract_file_key("") is None

    def test_random_url_rejected(self):
        assert figma.extract_file_key("https://example.com/file/aBcD1234EFgh5678") is None


class TestRequest:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "")
        with pytest.raises(figma.FigmaError, match="FIGMA_TOKEN is not set"):
            figma._request("/files/abc")

    def test_404_raises_friendly(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        resp = httpx.Response(404, text="Not Found", request=httpx.Request("GET", "x"))
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: resp)
        with pytest.raises(figma.FigmaError, match="not found or not accessible"):
            figma._request("/files/abc")

    def test_403_raises_friendly(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        resp = httpx.Response(403, text="Forbidden", request=httpx.Request("GET", "x"))
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: resp)
        with pytest.raises(figma.FigmaError, match="403"):
            figma._request("/files/abc")

    def test_network_error_raises_friendly(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        def boom(*a, **kw):
            raise httpx.ConnectError("nope")
        monkeypatch.setattr(httpx, "get", boom)
        with pytest.raises(figma.FigmaError, match="Could not reach Figma"):
            figma._request("/files/abc")


class TestWalkText:
    def test_extracts_frame_names_and_text(self):
        document = {
            "type": "DOCUMENT",
            "name": "Document",
            "children": [
                {
                    "type": "CANVAS",
                    "name": "Page 1",
                    "children": [
                        {
                            "type": "FRAME",
                            "name": "Hero",
                            "children": [
                                {"type": "TEXT", "name": "Title", "characters": "Welcome"},
                                {"type": "TEXT", "name": "Body", "characters": "Hello world"},
                            ],
                        }
                    ],
                }
            ],
        }
        result = figma.walk_text(document)
        joined = "\n".join(result)
        assert "Page 1" in joined
        assert "Hero" in joined
        assert "Welcome" in joined
        assert "Hello world" in joined

    def test_skips_text_nodes_without_characters(self):
        document = {
            "type": "DOCUMENT",
            "children": [
                {"type": "TEXT", "name": "Empty", "characters": ""},
            ],
        }
        assert figma.walk_text(document) == []

    def test_handles_missing_children(self):
        document = {"type": "DOCUMENT", "name": "Document"}
        assert figma.walk_text(document) == []


class TestRenderFileSummary:
    def test_includes_metadata_and_text(self):
        payload = {
            "name": "My Spec",
            "lastModified": "2026-05-07T12:00:00Z",
            "document": {
                "type": "DOCUMENT",
                "children": [
                    {"type": "CANVAS", "name": "Cover", "children": [
                        {"type": "TEXT", "characters": "Goals and non-goals"},
                    ]},
                ],
            },
        }
        out = figma.render_file_summary(payload)
        assert "My Spec" in out
        assert "2026-05-07" in out
        assert "Cover" in out
        assert "Goals and non-goals" in out

    def test_truncates_long_content(self):
        big_text = "x" * 20000
        payload = {
            "name": "Big",
            "document": {
                "type": "DOCUMENT",
                "children": [{"type": "TEXT", "characters": big_text}],
            },
        }
        out = figma.render_file_summary(payload)
        assert "truncated" in out
        assert len(out) < 10000


class TestReadFigmaFileTool:
    def test_returns_friendly_error_on_token_missing(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "")
        result = figma.read_figma_file.handler(file_key="aBcD1234EFgh5678")
        assert "FIGMA_TOKEN is not set" in result

    def test_uses_extracted_key_from_url(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        captured = {}
        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return httpx.Response(
                200,
                json={"name": "X", "document": {"type": "DOCUMENT", "children": []}},
                request=httpx.Request("GET", url),
            )
        monkeypatch.setattr(httpx, "get", fake_get)

        figma.read_figma_file.handler(
            file_key="https://www.figma.com/file/abcDEF123456/My-Doc"
        )
        assert "abcDEF123456" in captured["url"]
        assert captured["params"]["depth"] == 2


class TestReadFigmaNodeTool:
    def test_renders_node_payload(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        payload = {
            "nodes": {
                "1:23": {
                    "document": {
                        "type": "FRAME",
                        "name": "Architecture",
                        "children": [
                            {"type": "TEXT", "characters": "client → server"},
                        ],
                    }
                }
            }
        }
        resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "x"))
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: resp)

        out = figma.read_figma_node.handler(file_key="abc1234567", node_id="1:23")
        assert "Architecture" in out
        assert "client → server" in out

    def test_missing_node_returns_message(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")
        resp = httpx.Response(
            200, json={"nodes": {"1:23": None}}, request=httpx.Request("GET", "x")
        )
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: resp)
        out = figma.read_figma_node.handler(file_key="abc1234567", node_id="1:23")
        assert "not found" in out


class TestConnectSourceFigma:
    def test_invalid_identifier_rejected(self):
        from mimir_agent.tools.sources import connect_source
        result = connect_source.handler("figma_file", "not-a-real-key")
        assert "not a valid Figma" in result

    def test_token_missing_returns_friendly_error(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "")
        from mimir_agent.tools.sources import connect_source
        result = connect_source.handler("figma_file", "aBcD1234EFgh5678")
        assert "FIGMA_TOKEN is not set" in result

    def test_full_flow_validates_ingests_and_remembers(self, monkeypatch):
        monkeypatch.setattr(figma.config, "FIGMA_TOKEN", "tok")

        calls: list[dict] = []
        def fake_get(url, **kwargs):
            calls.append({"url": url, "params": kwargs.get("params")})
            return httpx.Response(
                200,
                json={
                    "name": "Spec",
                    "lastModified": "2026-05-07T00:00:00Z",
                    "document": {
                        "type": "DOCUMENT",
                        "children": [
                            {"type": "CANVAS", "name": "Page", "children": [
                                {"type": "TEXT", "characters": "ingested body"},
                            ]},
                        ],
                    },
                },
                request=httpx.Request("GET", url),
            )
        monkeypatch.setattr(httpx, "get", fake_get)

        with (
            patch("mimir_agent.tools.sources.db") as mock_db,
            patch("mimir_agent.tools.memory.remember") as mock_remember,
        ):
            mock_db.add_source.return_value = True
            from mimir_agent.tools.sources import connect_source
            result = connect_source.handler(
                "figma_file",
                "https://www.figma.com/file/aBcD1234EFgh5678/Spec",
            )

        assert "Connected" in result
        # depth=0 validate, then depth=2 ingest — two API calls
        assert [c["params"]["depth"] for c in calls] == [0, 2]
        # add_source got called with the normalized bare key, not the URL
        args, kwargs = mock_db.add_source.call_args
        assert args[0] == "figma_file"
        assert args[1] == "aBcD1234EFgh5678"
        # the file content was mirrored into memory
        mock_remember.handler.assert_called_once()
        remember_kwargs = mock_remember.handler.call_args.kwargs
        assert remember_kwargs["key"] == "figma:aBcD1234EFgh5678"
        assert "ingested body" in remember_kwargs["content"]
