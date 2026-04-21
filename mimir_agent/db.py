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
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (type, identifier)
            )
        """)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS eval_records (
                id SERIAL PRIMARY KEY,
                thread_ref TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                asker TEXT NOT NULL,
                outcome TEXT,
                evidence TEXT,
                confidence REAL,
                feedback TEXT,
                feedback_at TIMESTAMPTZ,
                classified_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    _backfill_embeddings()
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

def _seed_sources_from_config():
    """Seed sources table from env vars on first boot (won't duplicate)."""
    for repo in config.GITHUB_REPOS:
        add_source("github_repo", repo)
    for doc_id in config.GOOGLE_DOC_IDS:
        add_source("google_doc", doc_id)
    for key in config.FIGMA_FILE_KEYS:
        add_source("figma_file", key)


def add_source(source_type: str, identifier: str, label: str | None = None) -> bool:
    """Add a connected source. Returns True if added, False if already exists."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (type, identifier, label)
            VALUES (%s, %s, %s)
            ON CONFLICT (type, identifier) DO NOTHING
            """,
            (source_type, identifier, label),
        )
        return cur.rowcount > 0


def remove_source(source_type: str, identifier: str) -> bool:
    """Remove a connected source. Returns True if removed."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sources WHERE type = %s AND identifier = %s",
            (source_type, identifier),
        )
        return cur.rowcount > 0


def list_sources(source_type: str | None = None) -> list[tuple[str, str, str | None]]:
    """List connected sources, optionally filtered by type."""
    conn = _get_conn()
    with conn.cursor() as cur:
        if source_type:
            cur.execute(
                "SELECT type, identifier, label FROM sources WHERE type = %s ORDER BY identifier",
                (source_type,),
            )
        else:
            cur.execute("SELECT type, identifier, label FROM sources ORDER BY type, identifier")
        return cur.fetchall()


def get_github_repos() -> list[str]:
    """Get all connected GitHub repos (from DB + config)."""
    repos = set(config.GITHUB_REPOS)
    for _, identifier, _ in list_sources("github_repo"):
        repos.add(identifier)
    return sorted(repos)


def clear_memories() -> int:
    """Delete all memories. Returns count of deleted rows."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories")
        return cur.rowcount


def clear_sources() -> int:
    """Delete all sources. Returns count of deleted rows."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sources")
        count = cur.rowcount
    _seed_sources_from_config()
    return count


def memory_count() -> int:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM memories")
        return cur.fetchone()[0]


# --- Eval records ---

def create_eval_record(thread_ref: str, channel: str, question: str, answer: str, asker: str) -> int:
    """Create an eval record for a thread. Returns the record ID."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eval_records (thread_ref, channel, question, answer, asker)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (thread_ref) DO UPDATE
                SET question = %s, answer = %s
            RETURNING id
            """,
            (thread_ref, channel, question, answer, asker, question, answer),
        )
        return cur.fetchone()[0]


def classify_eval_record(thread_ref: str, outcome: str, evidence: str, confidence: float) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eval_records
            SET outcome = %s, evidence = %s, confidence = %s, classified_at = NOW()
            WHERE thread_ref = %s
            """,
            (outcome, evidence, confidence, thread_ref),
        )


def record_feedback(thread_ref: str, feedback: str) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eval_records
            SET feedback = %s, feedback_at = NOW()
            WHERE thread_ref = %s
            """,
            (feedback, thread_ref),
        )


def get_eval_record(thread_ref: str) -> dict | None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM eval_records WHERE thread_ref = %s",
            (thread_ref,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in cur.description]
        return dict(zip(cols, row))


def get_unclassified_records(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM eval_records
            WHERE classified_at IS NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_feedback_candidates(limit: int = 10) -> list[dict]:
    """Get classified records eligible for solicited feedback (no feedback yet)."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM eval_records
            WHERE classified_at IS NOT NULL
              AND feedback IS NULL
            ORDER BY classified_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
