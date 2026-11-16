import os
import threading
from unittest.mock import MagicMock, call, patch

import pytest


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


class TestSetChannelProject:
    @staticmethod
    def _run(channel_id, project_name):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mimir_agent.db.psycopg2.connect", return_value=mock_conn), \
                patch("mimir_agent.db._conn", None):
            from mimir_agent.db import set_channel_project
            set_channel_project(channel_id, project_name)
        return mock_conn, mock_cursor

    def test_remapping_a_channel_releases_it_from_its_old_project(self):
        """channel_id is UNIQUE and the upsert only handles a clash on name,
        so the channel has to be cleared from its old project first."""
        _, mock_cursor = self._run("C123", "newproj")

        statements = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert "SET channel_id = NULL" in statements[1]
        assert mock_cursor.execute.call_args_list[1][0][1] == ("C123", "newproj")
        assert "INSERT INTO projects" in statements[2]

    def test_remap_is_one_locked_transaction_on_its_own_connection(self):
        """Release and upsert run as one transaction, serialised per channel,
        so two remaps of a channel can't interleave and both claim it."""
        mock_conn, mock_cursor = self._run("C123", "newproj")

        statements = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert len(statements) == 3
        assert "pg_advisory_xact_lock" in statements[0]
        assert mock_cursor.execute.call_args_list[0][0][1] == ("C123",)
        # Not the shared autocommit connection: a transaction, committed
        # on leaving `with conn`, then closed.
        assert mock_conn.autocommit is not True
        mock_conn.__enter__.assert_called_once()
        mock_conn.__exit__.assert_called_once()
        assert mock_conn.__exit__.call_args[0][0] is None
        mock_conn.close.assert_called_once()


# Needs a real Postgres: set MIMIR_TEST_DATABASE_URL to one you don't mind
# having its `projects` table dropped. Skipped otherwise.
_PG_URL = os.environ.get("MIMIR_TEST_DATABASE_URL")


@pytest.mark.skipif(not _PG_URL, reason="MIMIR_TEST_DATABASE_URL not set")
class TestSetChannelProjectConcurrently:
    @pytest.fixture
    def pg(self, monkeypatch):
        import psycopg2

        from mimir_agent import config, db

        monkeypatch.setattr(config, "DATABASE_URL", _PG_URL)
        monkeypatch.setattr(db, "_conn", None)
        conn = psycopg2.connect(_PG_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS projects")
            cur.execute("""
                CREATE TABLE projects (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    channel_id TEXT UNIQUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        yield conn
        with conn.cursor() as cur:
            cur.execute("DROP TABLE projects")
        conn.close()
        if db._conn is not None:
            db._conn.close()

    def test_racing_remaps_of_one_channel_do_not_violate_unique(self, pg):
        import psycopg2

        from mimir_agent.db import set_channel_project

        threads, rounds = 8, 25
        barrier = threading.Barrier(threads)
        errors = []

        def remap(i):
            barrier.wait()
            for r in range(rounds):
                try:
                    set_channel_project("C123", f"proj{(i + r) % threads}")
                except psycopg2.Error as e:
                    errors.append(e)

        workers = [threading.Thread(target=remap, args=(i,)) for i in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        assert errors == []
        with pg.cursor() as cur:
            cur.execute("SELECT count(*) FROM projects WHERE channel_id = 'C123'")
            assert cur.fetchone()[0] == 1
