"""Pure SQLite + sqlite-vec access layer. No business logic lives here."""

import os
import sqlite3
from pathlib import Path

import sqlite_vec

from indy.config import DB_PATH
from indy.config import EMBEDDING_DIM
from indy.config import WORKING_DB_PATH


class IndexNotFoundError(Exception):
    """There is no database at DB_PATH to read."""


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def index_size_bytes() -> int:
    """Size of the current index, or 0 if this machine has none yet."""
    return DB_PATH.stat().st_size if DB_PATH.exists() else 0


def open_working_db() -> sqlite3.Connection:
    """Start a working copy of the index and open it for writing.

    indy.db itself is never written. It is seeded into a copy here and the finished copy is
    renamed over it by commit_working_db(), so the file on disk is replaced whole and is
    never open for writing — the two properties that make it safe to replicate, and the two
    it lacked when a syncer corrupted the index across the fleet.

    VACUUM INTO rather than a file copy: it reads inside a transaction, so the seed is one
    instant of the database and needs no -wal alongside it to be complete. A crashed run now
    costs only the working copy, since the index it was built from was never touched.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    discard_working_files()

    if DB_PATH.exists():
        source = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        try:
            source.execute('VACUUM INTO ?', (str(WORKING_DB_PATH),))
        finally:
            source.close()

    conn = sqlite3.connect(WORKING_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    _load_vec_extension(conn)
    init_schema(conn)
    return conn


def commit_working_db(conn: sqlite3.Connection) -> None:
    """Finish the working copy and swap it into place as the index.

    Delete journal mode before the rename is what leaves the result complete on its own: the
    WAL is folded back in and the sidecars go, so nothing is left beside indy.db that a copy
    of it could be missing.

    The outgoing database's sidecars are removed *before* the rename, not after. A -wal
    belongs to one specific database, and SQLite will adopt one it finds beside a file as
    that file's live tail: a read then answers out of the old database and never sees the new
    one, with no error anywhere. Removing them first means a crash mid-swap can only leave
    the previous index missing its last commits — stale, which a re-index fixes, rather than
    silently answering from a database that no longer exists.
    """
    conn.commit()
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.close()
    discard_sidecars(DB_PATH)
    os.replace(WORKING_DB_PATH, DB_PATH)


def discard_working_db(conn: sqlite3.Connection) -> None:
    """Abandon the working copy, leaving the index as it was before the run."""
    conn.close()
    discard_working_files()


def discard_working_files() -> None:
    """Remove the working copy, including any a killed run left behind."""
    WORKING_DB_PATH.unlink(missing_ok=True)
    discard_sidecars(WORKING_DB_PATH)


def discard_sidecars(db_path: Path) -> None:
    """Remove a database's -wal and -shm, which describe only that database."""
    for suffix in ('-wal', '-shm'):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)


def get_read_db() -> sqlite3.Connection:
    """Open the index read-only, at the same path a write would use.

    Read-only is a correctness requirement rather than an optimization. A normal connection
    to a WAL database writes to the main file when the last connection closes — SQLite runs
    a passive checkpoint — so on a machine that only ever searches, `indy search` still
    modified the file, and every search made that machine's copy diverge from the one a
    syncer had sent it. Against a finished index, which is in delete journal mode, a
    read-only connection writes nothing and creates no sidecar at all.
    """
    if not DB_PATH.exists():
        raise IndexNotFoundError(f'No index at {DB_PATH}. Run `indy index`, or wait for a peer to deliver one.')
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    _load_vec_extension(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
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

        CREATE TABLE IF NOT EXISTS symbol_reference (
            id            INTEGER PRIMARY KEY,
            source_file   TEXT NOT NULL,
            source_symbol TEXT,
            target_symbol TEXT NOT NULL,
            target_module TEXT,
            ref_type      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_symref_source ON symbol_reference(source_file);
        CREATE INDEX IF NOT EXISTS idx_symref_target ON symbol_reference(target_symbol);
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
    repos: set[str] | None = None,
) -> list[dict]:
    """KNN vector search. Filters by repo/language in Python after oversampling.

    repo: exact single-repo filter (existing behavior).
    repos: set of repo names to include (for owned/reference filtering).
    If both are set, repo takes precedence.
    """
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
        if repos is not None and chunk['repo'] not in repos:
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


def get_error_files(conn: sqlite3.Connection) -> list[dict]:
    """Return all files whose last index attempt did not succeed."""
    rows = conn.execute(
        "SELECT repo, file_path, status, indexed_at FROM indexed_file WHERE status != 'ok' ORDER BY repo, file_path"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_error_files(conn: sqlite3.Connection) -> int:
    """Remove all indexed_file records with error status. Returns count deleted."""
    cursor = conn.execute("DELETE FROM indexed_file WHERE status != 'ok'")
    conn.commit()
    return cursor.rowcount


def get_index_stats(conn: sqlite3.Connection) -> dict:
    total_files = conn.execute("SELECT COUNT(*) FROM indexed_file WHERE status = 'ok'").fetchone()[0]
    repo_count = conn.execute("SELECT COUNT(DISTINCT repo) FROM indexed_file WHERE status = 'ok'").fetchone()[0]
    total_chunks = conn.execute('SELECT COUNT(*) FROM chunk').fetchone()[0]
    error_files = conn.execute("SELECT COUNT(*) FROM indexed_file WHERE status != 'ok'").fetchone()[0]
    return {
        'total_files': total_files,
        'repo_count': repo_count,
        'total_chunks': total_chunks,
        'error_files': error_files,
    }


def get_recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Return the most recent index runs, newest first."""
    rows = conn.execute(
        'SELECT repo, started_at, files_scanned, files_updated, chunks_added FROM index_run ORDER BY id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_repo_scan_history(conn: sqlite3.Connection, repo: str, limit: int = 20) -> list[dict]:
    """Return scan history for a specific repo, oldest first (for charting)."""
    rows = conn.execute(
        'SELECT started_at, files_scanned, files_updated, chunks_added FROM index_run WHERE repo = ? ORDER BY id DESC LIMIT ?',
        (repo, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


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


# ── symbol_reference ──────────────────────────────────────────────────────────


def delete_file_references(conn: sqlite3.Connection, file_path: str) -> None:
    conn.execute('DELETE FROM symbol_reference WHERE source_file = ?', (file_path,))


def insert_references(conn: sqlite3.Connection, refs: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO symbol_reference (source_file, source_symbol, target_symbol, target_module, ref_type)
        VALUES (:source_file, :source_symbol, :target_symbol, :target_module, :ref_type)
        """,
        refs,
    )


def get_symbol_callers(conn: sqlite3.Connection, target_symbol: str, repo: str | None = None) -> list[dict]:
    """Return all references that call target_symbol (i.e. what calls it)."""
    if repo:
        rows = conn.execute(
            """
            SELECT sr.*
            FROM symbol_reference sr
            JOIN indexed_file f ON sr.source_file = f.file_path
            WHERE sr.target_symbol = ? AND sr.ref_type = 'call' AND f.repo = ?
            ORDER BY sr.source_file, sr.source_symbol
            """,
            (target_symbol, repo),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM symbol_reference WHERE target_symbol = ? AND ref_type = 'call' ORDER BY source_file, source_symbol",
            (target_symbol,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_symbol_callees(conn: sqlite3.Connection, source_symbol: str, repo: str | None = None) -> list[dict]:
    """Return all calls made by source_symbol (i.e. what it calls)."""
    if repo:
        rows = conn.execute(
            """
            SELECT sr.*
            FROM symbol_reference sr
            JOIN indexed_file f ON sr.source_file = f.file_path
            WHERE sr.source_symbol = ? AND sr.ref_type = 'call' AND f.repo = ?
            ORDER BY sr.target_symbol
            """,
            (source_symbol, repo),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM symbol_reference WHERE source_symbol = ? AND ref_type = 'call' ORDER BY target_symbol",
            (source_symbol,),
        ).fetchall()
    return [dict(row) for row in rows]
