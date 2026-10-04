import logging
import threading

import psycopg2
from pgvector.psycopg2 import register_vector

from mimir_agent import config

logger = logging.getLogger(__name__)

_conn = None
_initialized = False

# The SDK runs tool tasks concurrently (0.3.1), so two threads can reach this
# at once. psycopg2 serialises statements on one connection and every caller
# below takes its own cursor, so sharing the connection is fine — what is not
# fine is two threads both finding it None and both opening one, which leaks
# whichever loses.
_conn_lock = threading.Lock()


def _get_conn():
    global _conn
    with _conn_lock:
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(config.DATABASE_URL)
            _conn.autocommit = True
            if _initialized:
                register_vector(_conn)
        return _conn


def init():
    """Create tables with pgvector support if they don't exist."""
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
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding vector({config.EMBEDDING_DIMENSIONS}),
                project TEXT NOT NULL DEFAULT 'default',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (key, project)
            )
        """)
        # Migrate existing tables
        cur.execute(f"""
            ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS embedding vector({config.EMBEDDING_DIMENSIONS})
        """)
        cur.execute("""
            ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS project TEXT NOT NULL DEFAULT 'default'
        """)
        # Migrate unique constraint from (key) to (key, project)
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'memories_key_key'
                ) THEN
                    ALTER TABLE memories DROP CONSTRAINT memories_key_key;
                END IF;
            END $$;
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS memories_key_project_idx
            ON memories (key, project)
        """)
        # Drop NOT NULL on columns added by Norns server (agent_id, tenant_id, etc.)
        cur.execute("""
            DO $$
            DECLARE
                col TEXT;
            BEGIN
                FOR col IN
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'memories'
                    AND column_name NOT IN ('id', 'key', 'content', 'embedding', 'project', 'created_at', 'updated_at')
                    AND is_nullable = 'NO'
                LOOP
                    EXECUTE format('ALTER TABLE memories ALTER COLUMN %I DROP NOT NULL', col);
                END LOOP;
            END $$;
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
                project TEXT NOT NULL DEFAULT 'default',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (type, identifier, project)
            )
        """)
        cur.execute("""
            ALTER TABLE sources
            ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE
        """)
        cur.execute("""
            ALTER TABLE sources
            ADD COLUMN IF NOT EXISTS project TEXT NOT NULL DEFAULT 'default'
        """)
        # Migrate unique constraint to include project
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'sources_type_identifier_key'
                ) THEN
                    ALTER TABLE sources DROP CONSTRAINT sources_type_identifier_key;
                END IF;
            END $$;
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS sources_type_identifier_project_idx
            ON sources (type, identifier, project)
        """)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                channel_id TEXT UNIQUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS steward_asks (
                run_id BIGINT PRIMARY KEY,
                channel TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                question TEXT NOT NULL,
                posted_at TIMESTAMPTZ DEFAULT NOW(),
                answered_at TIMESTAMPTZ,
                reported_at TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS steward_asks_thread_idx
            ON steward_asks (channel, thread_ts)
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


def upsert_memory(key: str, content: str, embedding: list[float], project: str = "default") -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (key, content, embedding, project, updated_at)
            VALUES (%s, %s, %s::vector, %s, NOW())
            ON CONFLICT (key, project) DO UPDATE
                SET content = %s, embedding = %s::vector, updated_at = NOW()
            """,
            (key, content, embedding, project, content, embedding),
        )


# Memories stored under _global are always included in project-scoped searches.
GLOBAL_PROJECT = "_global"

# Recency decay: score = similarity * (floor + (1 - floor) * 2^(-age / half_life)),
# so a stale memory loses at most (1 - RECENCY_FLOOR) of its similarity score.
RECENCY_FLOOR = 0.7

# Re-ranks the top vector-search candidates (inner query stays HNSW-friendly)
# by similarity decayed with age. exp(-ln2 * age / half_life) == 2^(-age / half_life).
_RANKED_SEARCH_SQL = """
    SELECT key, content,
           similarity * (%s + %s * exp(-0.6931471805599453 * age_days / %s)) AS score,
           project
    FROM (
        SELECT key, content, project,
               1 - (embedding <=> %s::vector) AS similarity,
               extract(epoch FROM (NOW() - updated_at)) / 86400.0 AS age_days
        FROM memories
        WHERE embedding IS NOT NULL{project_filter}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    ) AS candidates
    ORDER BY score DESC
    LIMIT %s
"""


def search_memories(
    query_embedding: list[float],
    limit: int = 10,
    project: str | None = None,
) -> list[tuple[str, str, float, str]]:
    """Search memories by vector similarity with recency-decayed ranking.

    When project is given, searches that project + _global memories.
    When project is None, searches all projects.
    Returns (key, content, score, project) tuples, where score is cosine
    similarity discounted by how long ago the memory was last updated
    (half-life config.MEMORY_HALF_LIFE_DAYS).
    """
    conn = _get_conn()
    candidate_pool = max(limit * 4, 40)
    decay_params = (RECENCY_FLOOR, 1 - RECENCY_FLOOR, config.MEMORY_HALF_LIFE_DAYS)
    with conn.cursor() as cur:
        if project:
            cur.execute(
                _RANKED_SEARCH_SQL.format(project_filter=" AND project IN (%s, %s)"),
                (*decay_params, query_embedding, project, GLOBAL_PROJECT,
                 query_embedding, candidate_pool, limit),
            )
        else:
            cur.execute(
                _RANKED_SEARCH_SQL.format(project_filter=""),
                (*decay_params, query_embedding, query_embedding, candidate_pool, limit),
            )
        results = cur.fetchall()
        if results:
            return results

        # Fall back if no embedded rows exist yet
        if project:
            cur.execute(
                "SELECT key, content, 0.0 AS similarity, project FROM memories WHERE project IN (%s, %s) ORDER BY updated_at DESC LIMIT %s",
                (project, GLOBAL_PROJECT, limit),
            )
        else:
            cur.execute(
                "SELECT key, content, 0.0 AS similarity, project FROM memories ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
        return cur.fetchall()


# --- Sources ---

def _seed_default_sources():
    """Seed the always-on default sources (public repos shipped with every install).

    Also creates _global memory entries so search_memory finds them from any project.
    """
    from mimir_agent.embeddings import get_embedding

    for source_type, identifier, label in config.DEFAULT_SOURCES:
        add_source(source_type, identifier, label=label, is_default=True, project=GLOBAL_PROJECT)
        # Ensure a _global memory entry exists for each default source
        key = f"{source_type}:{identifier}"
        content = f"Default source: {label or identifier} ({source_type}: {identifier})"
        embedding = get_embedding(f"{key} {content}")
        upsert_memory(key, content, embedding, project=GLOBAL_PROJECT)


def _seed_sources_from_config():
    """Seed sources table from env vars on first boot (won't duplicate)."""
    for repo in config.GITHUB_REPOS:
        add_source("github_repo", repo)


def add_source(
    source_type: str,
    identifier: str,
    label: str | None = None,
    is_default: bool = False,
    project: str = "default",
) -> bool:
    """Add a connected source. Returns True if added, False if already exists."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (type, identifier, label, is_default, project)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (type, identifier, project) DO NOTHING
            """,
            (source_type, identifier, label, is_default, project),
        )
        return cur.rowcount > 0


def remove_source(source_type: str, identifier: str, project: str = "default") -> bool:
    """Remove a connected source. Default sources cannot be removed."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sources WHERE type = %s AND identifier = %s AND project = %s AND is_default = FALSE",
            (source_type, identifier, project),
        )
        return cur.rowcount > 0


def list_sources(
    source_type: str | None = None,
    user_only: bool = False,
    project: str | None = None,
) -> list[tuple[str, str, str | None, bool, str]]:
    """List connected sources. Each row: (type, identifier, label, is_default, project)."""
    conn = _get_conn()
    clauses = []
    params: list = []
    if source_type:
        clauses.append("type = %s")
        params.append(source_type)
    if user_only:
        clauses.append("is_default = FALSE")
    if project:
        clauses.append("(project IN (%s, %s) OR is_default = TRUE)")
        params.extend([project, GLOBAL_PROJECT])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT type, identifier, label, is_default, project FROM sources {where} "
            "ORDER BY is_default DESC, type, identifier",
            params,
        )
        return cur.fetchall()


def user_source_count(project: str | None = None) -> int:
    """Count sources the user has registered (excludes always-on defaults)."""
    conn = _get_conn()
    with conn.cursor() as cur:
        if project:
            cur.execute(
                "SELECT count(*) FROM sources WHERE is_default = FALSE AND project = %s",
                (project,),
            )
        else:
            cur.execute("SELECT count(*) FROM sources WHERE is_default = FALSE")
        return cur.fetchone()[0]


def get_github_repos(project: str | None = None) -> list[str]:
    """Get all connected GitHub repos (defaults + user + env config)."""
    repos = set(config.GITHUB_REPOS)
    for _, identifier, _, _, _ in list_sources("github_repo", project=project):
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


# --- Projects ---

def set_channel_project(channel_id: str, project_name: str) -> None:
    """Map a Slack channel to a project. Creates the project if needed."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (name, channel_id)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET channel_id = %s
            """,
            (project_name, channel_id, channel_id),
        )


def get_project_for_channel(channel_id: str) -> str | None:
    """Look up which project a channel belongs to. Returns None if unmapped."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM projects WHERE channel_id = %s", (channel_id,))
        row = cur.fetchone()
        return row[0] if row else None


def list_projects() -> list[tuple[str, str | None]]:
    """List all projects. Returns (name, channel_id) tuples."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT name, channel_id FROM projects ORDER BY name")
        return cur.fetchall()


# --- The steward's open questions ---------------------------------------
#
# One row per parked run: which Slack thread carries its question, so a
# reply in that thread can be routed back to that run. The row is the
# record of "already posted", which is what keeps the poller from posting
# the same proposal every twenty seconds.


def record_steward_ask(run_id: int, channel: str, thread_ts: str, question: str) -> bool:
    """Claim a run as posted. False if it was already claimed, which is how
    two pollers racing on the same run resolve to one Slack message."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO steward_asks (run_id, channel, thread_ts, question)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (run_id, channel, thread_ts, question),
        )
        return cur.rowcount > 0


def steward_ask_posted(run_id: int) -> bool:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM steward_asks WHERE run_id = %s", (run_id,))
        return cur.fetchone() is not None


def steward_run_for_thread(channel: str, thread_ts: str) -> int | None:
    """The run waiting on this Slack thread, if any. Unanswered rows win:
    a thread may carry several questions from one run over its life."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id FROM steward_asks
            WHERE channel = %s AND thread_ts = %s
            ORDER BY answered_at NULLS FIRST, posted_at DESC
            LIMIT 1
            """,
            (channel, thread_ts),
        )
        row = cur.fetchone()
        return row[0] if row else None


def steward_thread_for_run(run_id: int) -> str | None:
    """The Slack thread a run's question was posted in, if it was posted."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT thread_ts FROM steward_asks WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        return row[0] if row else None


def mark_steward_ask_answered(run_id: int) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE steward_asks SET answered_at = NOW() WHERE run_id = %s", (run_id,)
        )


def forget_steward_ask(run_id: int) -> None:
    """Drop the claim so the question can be posted again — used when the
    Slack post succeeded but the row could not be written, and the reverse."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM steward_asks WHERE run_id = %s", (run_id,))


def steward_asks_awaiting_report() -> list[tuple[int, str, str]]:
    """Runs that were answered and whose outcome has not been posted back.

    This is how the loop closes without anyone watching it: the run carries
    on in Norns after the answer, and the next poll that finds it finished
    reports into the thread the question came from.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, channel, thread_ts FROM steward_asks
            WHERE answered_at IS NOT NULL AND reported_at IS NULL
            ORDER BY answered_at
            """
        )
        return cur.fetchall()


def mark_steward_ask_reported(run_id: int) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE steward_asks SET reported_at = NOW() WHERE run_id = %s", (run_id,)
        )
