import logging

import psycopg2
from pgvector.psycopg2 import register_vector

from mimir_agent import config

logger = logging.getLogger(__name__)

_conn = None
_initialized = False


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(config.DATABASE_URL)
        _conn.autocommit = True
        if _initialized:
            register_vector(_conn)
    return _conn


def init():
    """Create the memories table with pgvector support if it doesn't exist."""
    global _initialized
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    _initialized = True
    with conn.cursor() as cur:
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

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,
                type TEXT NOT NULL,
                identifier TEXT NOT NULL,
                label TEXT,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (type, identifier)
            )
        """)
        cur.execute("""
            ALTER TABLE sources
            ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE
        """)

    _backfill_embeddings()
    _seed_default_sources()
    _seed_sources_from_config()


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
                "UPDATE memories SET embedding = %s::vector WHERE id = %s",
                (emb, row_id),
            )
    logger.info("Backfill complete")


def upsert_memory(key: str, content: str, embedding: list[float]) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (key, content, embedding, updated_at)
            VALUES (%s, %s, %s::vector, NOW())
            ON CONFLICT (key) DO UPDATE
                SET content = %s, embedding = %s::vector, updated_at = NOW()
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
            SELECT key, content, 1 - (embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
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


# --- Sources ---

def _seed_default_sources():
    """Seed the always-on default sources (public repos shipped with every install)."""
    for source_type, identifier, label in config.DEFAULT_SOURCES:
        add_source(source_type, identifier, label=label, is_default=True)


def _seed_sources_from_config():
    """Seed sources table from env vars on first boot (won't duplicate)."""
    for repo in config.GITHUB_REPOS:
        add_source("github_repo", repo)


def add_source(
    source_type: str,
    identifier: str,
    label: str | None = None,
    is_default: bool = False,
) -> bool:
    """Add a connected source. Returns True if added, False if already exists."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (type, identifier, label, is_default)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (type, identifier) DO NOTHING
            """,
            (source_type, identifier, label, is_default),
        )
        return cur.rowcount > 0


def remove_source(source_type: str, identifier: str) -> bool:
    """Remove a connected source. Default sources cannot be removed."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sources WHERE type = %s AND identifier = %s AND is_default = FALSE",
            (source_type, identifier),
        )
        return cur.rowcount > 0


def list_sources(
    source_type: str | None = None,
    user_only: bool = False,
) -> list[tuple[str, str, str | None, bool]]:
    """List connected sources. Each row: (type, identifier, label, is_default)."""
    conn = _get_conn()
    clauses = []
    params: list = []
    if source_type:
        clauses.append("type = %s")
        params.append(source_type)
    if user_only:
        clauses.append("is_default = FALSE")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT type, identifier, label, is_default FROM sources {where} "
            "ORDER BY is_default DESC, type, identifier",
            params,
        )
        return cur.fetchall()


def user_source_count() -> int:
    """Count sources the user has registered (excludes always-on defaults)."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM sources WHERE is_default = FALSE")
        return cur.fetchone()[0]


def get_github_repos() -> list[str]:
    """Get all connected GitHub repos (defaults + user + env config)."""
    repos = set(config.GITHUB_REPOS)
    for _, identifier, _, _ in list_sources("github_repo"):
        repos.add(identifier)
    return sorted(repos)


def clear_memories() -> int:
    """Delete all memories. Returns count of deleted rows."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories")
        return cur.rowcount


def clear_sources() -> int:
    """Delete user-registered sources (defaults are preserved and re-seeded)."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sources WHERE is_default = FALSE")
        count = cur.rowcount
    _seed_sources_from_config()
    return count


def memory_count() -> int:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM memories")
        return cur.fetchone()[0]
