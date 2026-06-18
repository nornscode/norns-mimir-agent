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


def search_memories(
    query_embedding: list[float],
    limit: int = 10,
    project: str | None = None,
) -> list[tuple[str, str, float, str]]:
    """Search memories by vector similarity. Optionally filter by project.

    When project is given, searches that project + _global memories.
    When project is None, searches all projects.
    Returns (key, content, similarity, project) tuples.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        if project:
            cur.execute(
                """
                SELECT key, content, 1 - (embedding <=> %s::vector) AS similarity, project
                FROM memories
                WHERE embedding IS NOT NULL AND project IN (%s, %s)
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (query_embedding, project, GLOBAL_PROJECT, query_embedding, limit),
            )
        else:
            cur.execute(
                """
                SELECT key, content, 1 - (embedding <=> %s::vector) AS similarity, project
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
