"""Pure SQLite + sqlite-vec access layer. No business logic lives here."""

import sqlite3

import sqlite_vec

from indy.config import DB_PATH
from indy.config import EMBEDDING_DIM


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS indexed_file (
            id           INTEGER PRIMARY KEY,
            repo         TEXT NOT NULL,
            file_path    TEXT NOT NULL UNIQUE,
            language     TEXT,
            mtime        REAL NOT NULL,
            content_hash TEXT NOT NULL,
            chunk_count  INTEGER NOT NULL,
            indexed_at   TEXT NOT NULL,
            status       TEXT DEFAULT 'ok'
        );

        CREATE TABLE IF NOT EXISTS index_run (
            id            INTEGER PRIMARY KEY,
            repo          TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            files_scanned INTEGER,
            files_updated INTEGER,
            chunks_added  INTEGER,
            error         TEXT
        );

        CREATE TABLE IF NOT EXISTS chunk (
            id           INTEGER PRIMARY KEY,
            file_path    TEXT NOT NULL,
            repo         TEXT NOT NULL,
            language     TEXT,
            symbol_name  TEXT,
            symbol_type  TEXT,
            module       TEXT,
            text         TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding float[{EMBEDDING_DIM}] distance_metric=cosine
        );
    """)
    conn.commit()


# ── indexed_file ──────────────────────────────────────────────────────────────


def get_indexed_file(conn: sqlite3.Connection, file_path: str) -> dict | None:
    row = conn.execute('SELECT * FROM indexed_file WHERE file_path = ?', (file_path,)).fetchone()
    return dict(row) if row else None


def upsert_indexed_file(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT INTO indexed_file (repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status)
        VALUES (:repo, :file_path, :language, :mtime, :content_hash, :chunk_count, :indexed_at, :status)
        ON CONFLICT (file_path) DO UPDATE SET
            repo         = excluded.repo,
            language     = excluded.language,
            mtime        = excluded.mtime,
            content_hash = excluded.content_hash,
            chunk_count  = excluded.chunk_count,
            indexed_at   = excluded.indexed_at,
            status       = excluded.status
        """,
        data,
    )


# ── chunks + vectors ──────────────────────────────────────────────────────────


def delete_file_chunks(conn: sqlite3.Connection, file_path: str) -> None:
    """Remove all chunk rows and their vec_chunks entries for a given file."""
    chunk_ids = [row[0] for row in conn.execute('SELECT id FROM chunk WHERE file_path = ?', (file_path,)).fetchall()]
    for chunk_id in chunk_ids:
        conn.execute('DELETE FROM vec_chunks WHERE rowid = ?', (chunk_id,))
    conn.execute('DELETE FROM chunk WHERE file_path = ?', (file_path,))


def insert_chunk(conn: sqlite3.Connection, data: dict) -> int:
    """Insert a chunk row and return its new id."""
    cursor = conn.execute(
        """
        INSERT INTO chunk (file_path, repo, language, symbol_name, symbol_type, module, text)
        VALUES (:file_path, :repo, :language, :symbol_name, :symbol_type, :module, :text)
        """,
        data,
    )
    return cursor.lastrowid  # type: ignore[return-value]


def insert_chunk_embedding(conn: sqlite3.Connection, chunk_id: int, embedding: list[float]) -> None:
    """Store the float32 embedding for a chunk. rowid must match chunk.id."""
    conn.execute(
        'INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)',
        (chunk_id, sqlite_vec.serialize_float32(embedding)),
    )


def search_chunks(
    conn: sqlite3.Connection,
    embedding: list[float],
    repo: str | None = None,
    language: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """KNN vector search. Filters by repo/language in Python after oversampling."""
    # Oversample to ensure enough results survive metadata filtering
    candidate_limit = min(limit * 20, 1000)

    rows = conn.execute(
        """
        SELECT v.rowid, v.distance
        FROM vec_chunks v
        WHERE v.embedding MATCH ?
        ORDER BY v.distance
        LIMIT ?
        """,
        (sqlite_vec.serialize_float32(embedding), candidate_limit),
    ).fetchall()

    results = []
    for row in rows:
        chunk = conn.execute('SELECT * FROM chunk WHERE id = ?', (row['rowid'],)).fetchone()
        if chunk is None:
            continue
        if repo and chunk['repo'] != repo:
            continue
        if language and chunk['language'] != language:
            continue
        results.append({**dict(chunk), 'distance': row['distance']})
        if len(results) >= limit:
            break

    return results


def get_chunks_by_symbol(conn: sqlite3.Connection, name: str, repo: str | None = None) -> list[dict]:
    """Exact symbol_name lookup — no vector search."""
    if repo:
        rows = conn.execute('SELECT * FROM chunk WHERE symbol_name = ? AND repo = ?', (name, repo)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM chunk WHERE symbol_name = ?', (name,)).fetchall()
    return [dict(row) for row in rows]


# ── index_run ─────────────────────────────────────────────────────────────────


def start_index_run(conn: sqlite3.Connection, repo: str, started_at: str) -> int:
    cursor = conn.execute('INSERT INTO index_run (repo, started_at) VALUES (?, ?)', (repo, started_at))
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def finish_index_run(conn: sqlite3.Connection, run_id: int, data: dict) -> None:
    conn.execute(
        """
        UPDATE index_run
        SET finished_at   = :finished_at,
            files_scanned = :files_scanned,
            files_updated = :files_updated,
            chunks_added  = :chunks_added,
            error         = :error
        WHERE id = :id
        """,
        {**data, 'id': run_id},
    )


# ── reporting ─────────────────────────────────────────────────────────────────


def get_index_stats(conn: sqlite3.Connection) -> dict:
    total_files = conn.execute("SELECT COUNT(*) FROM indexed_file WHERE status = 'ok'").fetchone()[0]
    total_chunks = conn.execute('SELECT COUNT(*) FROM chunk').fetchone()[0]
    error_files = conn.execute("SELECT COUNT(*) FROM indexed_file WHERE status != 'ok'").fetchone()[0]
    last_run = conn.execute(
        'SELECT repo, started_at, finished_at, files_updated, chunks_added, error FROM index_run ORDER BY id DESC LIMIT 1'
    ).fetchone()
    return {
        'total_files': total_files,
        'total_chunks': total_chunks,
        'error_files': error_files,
        'last_run': dict(last_run) if last_run else None,
    }


def get_indexed_repos(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT repo,
               COUNT(*) AS file_count,
               SUM(chunk_count) AS chunk_count,
               MAX(indexed_at) AS last_indexed
        FROM indexed_file
        WHERE status = 'ok'
        GROUP BY repo
        ORDER BY repo
        """
    ).fetchall()
    return [dict(row) for row in rows]
