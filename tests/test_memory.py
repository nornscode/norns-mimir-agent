from unittest.mock import patch, MagicMock


class TestRemember:
    def test_stores_with_embedding(self):
        mock_embedding = [0.1, 0.2, 0.3]
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=mock_embedding) as mock_get_emb,
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            from mimir_agent.tools.memory import remember
            result = remember.handler("test_key", "test content")

        mock_get_emb.assert_called_once_with("test_key test content")
        mock_db.upsert_memory.assert_called_once_with("test_key", "test content", mock_embedding, project="default")
        assert "Remembered" in result
        assert "test_key" in result


class TestSearchMemory:
    def test_returns_formatted_results(self):
        mock_embedding = [0.1, 0.2, 0.3]
        mock_results = [
            ("norns_url", "https://github.com/amackera/norns", 0.95, "default"),
            ("cat_name", "Wally", 0.42, "default"),
        ]
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=mock_embedding),
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            mock_db.search_memories.return_value = mock_results
            from mimir_agent.tools.memory import search_memory
            result = search_memory.handler("norns repo")

        assert "norns_url" in result
        assert "0.95" in result
        assert "cat_name" in result

    def test_returns_no_results_message(self):
        with (
            patch("mimir_agent.tools.memory.get_embedding", return_value=[0.1]),
            patch("mimir_agent.tools.memory.db") as mock_db,
        ):
            mock_db.search_memories.return_value = []
            from mimir_agent.tools.memory import search_memory
            result = search_memory.handler("nonexistent")

        assert "No matching facts" in result
