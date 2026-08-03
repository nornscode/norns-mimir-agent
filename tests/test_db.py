import pytest
from unittest.mock import MagicMock, patch, call


class TestUpsertMemory:
    def test_upsert_uses_vector_cast(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db._get_conn", return_value=mock_conn):
            from mimir_agent.db import upsert_memory
            upsert_memory("test_key", "test_content", [0.1, 0.2, 0.3])

        sql = mock_cursor.execute.call_args[0][0]
        assert "::vector" in sql

    def test_search_uses_vector_cast(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("key", "content", 0.95)]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db._get_conn", return_value=mock_conn):
            from mimir_agent.db import search_memories
            results = search_memories([0.1, 0.2, 0.3])

        sql = mock_cursor.execute.call_args[0][0]
        assert "::vector" in sql
        assert len(results) == 1
        assert results[0][0] == "key"


class TestSearchMemories:
    def test_ranks_with_recency_decay(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("key", "content", 0.9, "default")]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db._get_conn", return_value=mock_conn):
            from mimir_agent.db import search_memories
            search_memories([0.1, 0.2], limit=5, project="myproj")

        sql = mock_cursor.execute.call_args[0][0]
        assert "exp(" in sql
        assert "ORDER BY score DESC" in sql
        # Candidate pool is wider than the requested limit
        params = mock_cursor.execute.call_args[0][1]
        assert 40 in params  # max(5 * 4, 40)
        assert params[-1] == 5


    def test_returns_empty_list(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [[], []]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db._get_conn", return_value=mock_conn):
            from mimir_agent.db import search_memories
            results = search_memories([0.1, 0.2])

        assert results == []

    def test_falls_back_to_keyword_search(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # First call (vector search) returns empty, second (fallback) returns results
        mock_cursor.fetchall.side_effect = [[], [("key", "content", 0.0)]]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db._get_conn", return_value=mock_conn):
            from mimir_agent.db import search_memories
            results = search_memories([0.1, 0.2])

        assert len(results) == 1
        assert mock_cursor.execute.call_count == 2
