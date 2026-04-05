import logging

import psycopg2
from pgvector.psycopg2 import register_vector

from mimir_agent import config

logger = logging.getLogger(__name__)

_conn = None


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(config.DATABASE_URL)
        _conn.autocommit = True
        register_vector(_conn)
    return _conn


def init():
    """Create the memories table with pgvector support if it doesn't exist."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id SERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                embedding vector({config.EMBEDDING_DIMENSIONS}),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Migrate existing tables that don't have the embedding column yet
        cur.execute(f"""
            ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS embedding vector({config.EMBEDDING_DIMENSIONS})
        """)
        cur.execute("""
            SELECT 1 FROM pg_indexes
            WHERE indexname = 'memories_embedding_idx'
        """)
        if not cur.fetchone():
            cur.execute("""
                CREATE INDEX memories_embedding_idx
                ON memories USING hnsw (embedding vector_cosine_ops)
            """)

    _backfill_embeddings()


def _backfill_embeddings():
    """Generate embeddings for any memories that don't have one yet."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, key, content FROM memories WHERE embedding IS NULL")
        rows = cur.fetchall()

    if not rows:
        return

    logger.info("Backfilling embeddings for %d memories", len(rows))
    from mimir_agent.embeddings import get_embeddings_batch

    texts = [f"{key} {content}" for _, key, content in rows]
    embeddings = get_embeddings_batch(texts)

    with conn.cursor() as cur:
        for (row_id, _, _), emb in zip(rows, embeddings):
            cur.execute(
                "UPDATE memories SET embedding = %s WHERE id = %s",
                (emb, row_id),
            )
    logger.info("Backfill complete")


def upsert_memory(key: str, content: str, embedding: list[float]) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (key, content, embedding, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
                SET content = %s, embedding = %s, updated_at = NOW()
            """,
            (key, content, embedding, content, embedding),
        )


def search_memories(query_embedding: list[float], limit: int = 10) -> list[tuple[str, str, float]]:
    """Search memories by vector similarity, with ILIKE fallback for un-embedded rows."""
    conn = _get_conn()
    with conn.cursor() as cur:
        # Try vector search first
        cur.execute(
            """
            SELECT key, content, 1 - (embedding <=> %s) AS similarity
            FROM memories
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (query_embedding, query_embedding, limit),
        )
        results = cur.fetchall()
        if results:
            return results

        # Fall back to keyword search if no embedded rows exist yet
        cur.execute(
            """
            SELECT key, content, 0.0 AS similarity
            FROM memories
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()
