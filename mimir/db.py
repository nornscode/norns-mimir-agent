import psycopg2

from mimir import config

_conn = None


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(config.DATABASE_URL)
        _conn.autocommit = True
    return _conn


def init():
    """Create the memories table if it doesn't exist."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id SERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


def upsert_memory(key: str, content: str) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (key, content, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET content = %s, updated_at = NOW()
            """,
            (key, content, content),
        )


def search_memories(query: str, limit: int = 10) -> list[tuple[str, str]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, content FROM memories
            WHERE key ILIKE %s OR content ILIKE %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (f"%{query}%", f"%{query}%", limit),
        )
        return cur.fetchall()
