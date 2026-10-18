"""Pure SQLite + sqlite-vec access layer. No business logic lives here."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Collection
from pathlib import Path

import sqlite_vec

from indy.config import DB_PATH
from indy.config import EMBEDDING_DIM
from indy.config import OVERLAY_DIR
from indy.config import WORKING_DB_PATH

# `indy watch` and the compaction after a full index both write the overlay.
OVERLAY_BUSY_SECONDS = 30.0


class IndexNotFoundError(Exception):
    """There is no database at DB_PATH to read."""


def load_vec_extension(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def file_uri(path: Path, mode: str = '') -> str:
    """A path as an SQLite URI, percent-encoded so a `?` or `#` in it is not read as a parameter."""
    uri = path.absolute().as_uri()
    return f'{uri}?mode={mode}' if mode else uri


def index_size_bytes() -> int:
    """Size of the current index, or 0 if this machine has none yet."""
    return DB_PATH.stat().st_size if DB_PATH.exists() else 0


def open_working_db() -> sqlite3.Connection:
    """Start a working copy of the index and open it for writing.

    indy.db itself is never written. It is seeded into a copy here and the finished copy is
    renamed over it by commit_working_db(), so the file on disk is replaced whole and is
    never open for writing. Those are the two properties that make it safe for a file syncer
    to replicate. Without them, a syncer copies blocks read at different moments, and every
    replica can end up corrupt.

    VACUUM INTO rather than a file copy: it reads inside a transaction, so the seed is one
    instant of the database and needs no -wal alongside it to be complete. A crashed run
    costs only the working copy, since the index it was built from was never touched.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    discard_working_files()

    if DB_PATH.exists():
        source = sqlite3.connect(file_uri(DB_PATH, 'ro'), uri=True)
        try:
            source.execute('VACUUM INTO ?', (str(WORKING_DB_PATH),))
        finally:
            source.close()

    conn = sqlite3.connect(WORKING_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    load_vec_extension(conn)
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


def open_base_readonly() -> sqlite3.Connection:
    """Open the index read-only, at the same path a write would use, and nothing beside it.

    Read-only is a correctness requirement rather than an optimization. A normal connection
    to a WAL database writes to the main file when the last connection closes — SQLite runs
    a passive checkpoint — so on a machine that only ever searches, `indy search` still
    modified the file, and every search made that machine's copy diverge from the one a
    syncer had sent it. Against a finished index, which is in delete journal mode, a
    read-only connection writes nothing and creates no sidecar at all.
    """
    if not DB_PATH.exists():
        raise IndexNotFoundError(f'No index at {DB_PATH}. Run `indy index`, or wait for a peer to deliver one.')
    conn = sqlite3.connect(file_uri(DB_PATH, 'ro'), uri=True)
    conn.row_factory = sqlite3.Row
    load_vec_extension(conn)
    return conn


def get_read_db() -> sqlite3.Connection:
    """The index as a read sees it: the base, with this machine's overlay laid over it if it has one."""
    conn = open_base_readonly()
    attach_overlay(conn)
    return conn


CONTENT_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS indexed_file (
        id           INTEGER PRIMARY KEY,
        repo         TEXT NOT NULL,
        file_path    TEXT NOT NULL UNIQUE,
        language     TEXT,
        mtime        REAL NOT NULL,
        content_hash TEXT NOT NULL,
        chunk_count  INTEGER NOT NULL,
        indexed_at   TEXT NOT NULL,
        status       TEXT DEFAULT 'ok',
        extractor_version INTEGER NOT NULL DEFAULT 0
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

    CREATE INDEX IF NOT EXISTS idx_chunk_symbol ON chunk(symbol_name);
    CREATE INDEX IF NOT EXISTS idx_chunk_file ON chunk(file_path);

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
    CREATE INDEX IF NOT EXISTS idx_symref_inherit ON symbol_reference(source_symbol) WHERE ref_type = 'inherit';
"""
"""The tables a file's content lives in, which the base and the overlay both hold."""


def add_missing_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    """CREATE TABLE IF NOT EXISTS never reaches a table built before the column existed."""
    if column not in {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')


SCIP_SCHEMA = """
    CREATE TABLE IF NOT EXISTS scip_index (
        repo        TEXT PRIMARY KEY,
        indexed_at  TEXT NOT NULL,
        status      TEXT NOT NULL,
        reason      TEXT,
        fingerprint TEXT,
        document_count  INTEGER NOT NULL DEFAULT 0,
        reference_count INTEGER NOT NULL DEFAULT 0,
        seconds     REAL
    );

    CREATE TABLE IF NOT EXISTS scip_document (
        file_path TEXT PRIMARY KEY,
        repo      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scip_document_repo ON scip_document(repo);

    CREATE TABLE IF NOT EXISTS scip_symbol (
        id        INTEGER PRIMARY KEY,
        repo      TEXT NOT NULL,
        symbol    TEXT NOT NULL,
        module    TEXT NOT NULL,
        name      TEXT NOT NULL,
        member    TEXT NOT NULL,
        kind      TEXT NOT NULL,
        file_path TEXT NOT NULL,
        line      INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scip_symbol_member ON scip_symbol(member);
    CREATE INDEX IF NOT EXISTS idx_scip_symbol_repo ON scip_symbol(repo);

    CREATE TABLE IF NOT EXISTS scip_occurrence (
        id            INTEGER PRIMARY KEY,
        repo          TEXT NOT NULL,
        file_path     TEXT NOT NULL,
        line          INTEGER NOT NULL,
        symbol        TEXT NOT NULL,
        member        TEXT NOT NULL,
        source_symbol TEXT,
        ref_type      TEXT NOT NULL,
        on_receiver   INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scip_occurrence_symbol ON scip_occurrence(symbol);
    CREATE INDEX IF NOT EXISTS idx_scip_occurrence_member ON scip_occurrence(member);
    CREATE INDEX IF NOT EXISTS idx_scip_occurrence_repo ON scip_occurrence(repo);

    CREATE TABLE IF NOT EXISTS scip_relationship (
        id            INTEGER PRIMARY KEY,
        repo          TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        kind          TEXT NOT NULL,
        target_symbol TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scip_relationship_symbol ON scip_relationship(symbol);
    CREATE INDEX IF NOT EXISTS idx_scip_relationship_repo ON scip_relationship(repo);
"""
"""What scip-python resolved in each owned Python repo. Only a full index writes it, so only the base holds it."""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        CONTENT_SCHEMA
        + SCIP_SCHEMA
        + """
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
        """
    )
    add_missing_column(conn, 'indexed_file', 'extractor_version', 'INTEGER NOT NULL DEFAULT 0')
    conn.commit()


# ── overlay ───────────────────────────────────────────────────────────────────


def init_overlay_schema(conn: sqlite3.Connection) -> None:
    """The content tables, plus what only the overlay records.

    A tombstone is a file deleted since the base was built: the base still holds its rows,
    and the tombstone is what hides them. `branch` is set on a linked worktree's files, which
    the base never holds, and is how a result says which branch it came from.
    """
    conn.executescript(
        CONTENT_SCHEMA
        + """
        CREATE TABLE IF NOT EXISTS tombstone (
            file_path  TEXT PRIMARY KEY,
            repo       TEXT NOT NULL,
            deleted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS overlay_state (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TEXT,
            base_stamp TEXT
        );
        """
    )
    add_missing_column(conn, 'indexed_file', 'branch', 'TEXT')
    conn.commit()


def overlay_path() -> Path:
    """The overlay belonging to the index at DB_PATH, named by that index's resolved path.

    One per index, so a read or an index run under INDY_DIR or another data_dir never sees the
    overlay of the index this machine usually reads.
    """
    return OVERLAY_DIR / f'{hashlib.sha256(str(DB_PATH.resolve()).encode()).hexdigest()[:16]}.db'


def base_stamp() -> str | None:
    """Which file is at DB_PATH now, as inode, size and mtime, or None when there is none.

    The base is only ever replaced whole, by a rename here or by a syncer delivering a copy,
    and either one changes the stamp. So a stamp names one base.
    """
    try:
        st = DB_PATH.stat()
    except FileNotFoundError:
        return None
    return f'{st.st_ino}:{st.st_size}:{st.st_mtime_ns}'


def open_overlay_db() -> sqlite3.Connection:
    """Open the overlay for writing, creating it on first use.

    WAL and written in place, which the base must never be. That is safe here and only here
    because the overlay lives in the cache directory, which nothing replicates: the property that
    corrupted the synced index was a file changing under a syncer, and no syncer reads this one.
    Opened as a URI so compaction can attach the base read-only beside it.
    """
    path = overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(file_uri(path), uri=True, timeout=OVERLAY_BUSY_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    load_vec_extension(conn)
    init_overlay_schema(conn)
    return conn


# Temp views named for the base's tables. SQLite resolves an unqualified name in the temp
# schema before main, so every query in this module reads base and overlay as one store
# without naming either. Overlay ids are negated, which keeps an id unique across both.
# `indexed_file` leaves out extractor_version: no read uses it, and a base built before
# the column existed would otherwise refuse the view.
OVERLAY_VIEWS = """
    CREATE TEMP VIEW shadowed AS
        SELECT file_path FROM overlay.indexed_file
        UNION ALL
        SELECT file_path FROM overlay.tombstone;

    CREATE TEMP VIEW indexed_file AS
        SELECT id, repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status, NULL AS branch
        FROM main.indexed_file
        WHERE file_path NOT IN (SELECT file_path FROM temp.shadowed)
        UNION ALL
        SELECT -id, repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status, branch
        FROM overlay.indexed_file;

    CREATE TEMP VIEW chunk AS
        SELECT id, file_path, repo, language, symbol_name, symbol_type, module, text, NULL AS branch
        FROM main.chunk
        WHERE file_path NOT IN (SELECT file_path FROM temp.shadowed)
        UNION ALL
        SELECT -c.id, c.file_path, c.repo, c.language, c.symbol_name, c.symbol_type, c.module, c.text, f.branch
        FROM overlay.chunk c JOIN overlay.indexed_file f ON f.file_path = c.file_path;

    CREATE TEMP VIEW symbol_reference AS
        SELECT id, source_file, source_symbol, target_symbol, target_module, ref_type, NULL AS branch
        FROM main.symbol_reference
        WHERE source_file NOT IN (SELECT file_path FROM temp.shadowed)
        UNION ALL
        SELECT -r.id, r.source_file, r.source_symbol, r.target_symbol, r.target_module, r.ref_type, f.branch
        FROM overlay.symbol_reference r JOIN overlay.indexed_file f ON f.file_path = r.source_file;
"""

# The overlay holds no SCIP rows, so a file it holds has none: `deps` reads that file's
# syntactic rows from the overlay instead of the base's links into content that changed.
SCIP_OVERLAY_VIEWS = """
    CREATE TEMP VIEW scip_document AS
        SELECT * FROM main.scip_document WHERE file_path NOT IN (SELECT file_path FROM temp.shadowed);

    CREATE TEMP VIEW scip_occurrence AS
        SELECT * FROM main.scip_occurrence WHERE file_path NOT IN (SELECT file_path FROM temp.shadowed);
"""


def has_scip_tables(conn: sqlite3.Connection) -> bool:
    """Whether the base was built by an indy that runs scip-python. An older base has no scip_* tables to read."""
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'scip_occurrence'").fetchone() is not None


def attach_overlay(conn: sqlite3.Connection) -> None:
    """Lay the overlay over a read connection to the base, if it was last reconciled with this base.

    For any file the overlay holds, as rows or as a tombstone, the base's rows for that path
    are hidden. An overlay reconciled with a different base is attached but not laid over this
    one. Its rows were compared with an index that has since been replaced, so they could hide
    newer content, and get_overlay_summary reports them set aside instead. A machine with no
    overlay reads the base exactly as before. Read-only like the base itself.
    """
    path = overlay_path()
    if not path.exists():
        return
    conn.execute('ATTACH DATABASE ? AS overlay', (file_uri(path, 'ro'),))
    reconciled = conn.execute('SELECT base_stamp FROM overlay.overlay_state').fetchone()
    if reconciled is not None and reconciled[0] == base_stamp():
        conn.executescript(OVERLAY_VIEWS)
        if has_scip_tables(conn):
            conn.executescript(SCIP_OVERLAY_VIEWS)


def has_overlay(conn: sqlite3.Connection) -> bool:
    """Whether an overlay is attached, laid over the base or not."""
    return any(row[1] == 'overlay' for row in conn.execute('PRAGMA database_list'))


def overlay_applied(conn: sqlite3.Connection) -> bool:
    """Whether reads on this connection see the overlay's rows in place of the base's."""
    return conn.execute("SELECT 1 FROM temp.sqlite_master WHERE type = 'view' AND name = 'shadowed'").fetchone() is not None


def get_tombstone(conn: sqlite3.Connection, file_path: str) -> dict | None:
    row = conn.execute('SELECT * FROM tombstone WHERE file_path = ?', (file_path,)).fetchone()
    return dict(row) if row else None


def put_tombstone(conn: sqlite3.Connection, file_path: str, repo: str, deleted_at: str) -> None:
    conn.execute(
        'INSERT INTO tombstone (file_path, repo, deleted_at) VALUES (?, ?, ?)'
        ' ON CONFLICT (file_path) DO UPDATE SET deleted_at = excluded.deleted_at',
        (file_path, repo, deleted_at),
    )


def clear_overlay_file(conn: sqlite3.Connection, file_path: str) -> bool:
    """Remove everything the overlay holds for one path. Returns whether it held anything."""
    held = conn.execute(
        'SELECT 1 FROM indexed_file WHERE file_path = ? UNION ALL SELECT 1 FROM tombstone WHERE file_path = ?', (file_path, file_path)
    ).fetchone()
    delete_file_chunks(conn, file_path)
    delete_file_references(conn, file_path)
    delete_indexed_file(conn, file_path)
    conn.execute('DELETE FROM tombstone WHERE file_path = ?', (file_path,))
    return held is not None


def insert_overlay_file(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT INTO indexed_file
            (repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status, extractor_version, branch)
        VALUES (:repo, :file_path, :language, :mtime, :content_hash, :chunk_count, :indexed_at, 'ok', :extractor_version, :branch)
        """,
        data,
    )


def mark_overlay_updated(conn: sqlite3.Connection, updated_at: str) -> None:
    conn.execute(
        'INSERT INTO overlay_state (id, updated_at) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET updated_at = excluded.updated_at',
        (updated_at,),
    )


def get_reconciled_stamp(conn: sqlite3.Connection) -> str | None:
    """The stamp of the base this overlay was last compacted against."""
    row = conn.execute('SELECT base_stamp FROM overlay_state').fetchone()
    return row[0] if row else None


def record_reconciled_stamp(conn: sqlite3.Connection, stamp: str | None) -> None:
    conn.execute(
        'INSERT INTO overlay_state (id, base_stamp) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET base_stamp = excluded.base_stamp',
        (stamp,),
    )


def get_chunk_vectors(conn: sqlite3.Connection, file_path: str) -> dict[str, bytes]:
    """Each of a file's chunks as text mapped to its stored embedding, from one store."""
    rows = conn.execute(
        'SELECT c.text, v.embedding FROM main.chunk c JOIN main.vec_chunks v ON v.rowid = c.id WHERE c.file_path = ?',
        (file_path,),
    )
    return {row[0]: row[1] for row in rows}


def get_file_states(conn: sqlite3.Connection, prefix: str) -> dict[str, tuple[str, float]]:
    """Content hash and mtime of every file one store holds under a path prefix."""
    rows = conn.execute(
        "SELECT file_path, content_hash, mtime FROM main.indexed_file WHERE status = 'ok' AND substr(file_path, 1, ?) = ?",
        (len(prefix), prefix),
    )
    return {row[0]: (row[1], row[2]) for row in rows}


def get_tombstoned_paths(conn: sqlite3.Connection, prefix: str) -> set[str]:
    return {row[0] for row in conn.execute('SELECT file_path FROM tombstone WHERE substr(file_path, 1, ?) = ?', (len(prefix), prefix))}


def get_paths_extracted_by_another_version(conn: sqlite3.Connection, prefix: str, version: int) -> set[str]:
    """Files under a prefix whose references some other extractor version produced."""
    rows = conn.execute(
        'SELECT file_path FROM indexed_file WHERE extractor_version != ? AND substr(file_path, 1, ?) = ?', (version, len(prefix), prefix)
    )
    return {row[0] for row in rows}


def get_overlay_paths(conn: sqlite3.Connection, prefix: str) -> list[str]:
    """Every path the overlay holds under a prefix, as rows or as a tombstone."""
    rows = conn.execute(
        'SELECT file_path FROM indexed_file WHERE substr(file_path, 1, ?) = ?'
        ' UNION SELECT file_path FROM tombstone WHERE substr(file_path, 1, ?) = ?',
        (len(prefix), prefix, len(prefix), prefix),
    )
    return [row[0] for row in rows]


def set_overlay_branch(conn: sqlite3.Connection, prefix: str, branch: str) -> int:
    return conn.execute(
        'UPDATE indexed_file SET branch = ? WHERE substr(file_path, 1, ?) = ? AND branch IS NOT ?', (branch, len(prefix), prefix, branch)
    ).rowcount


def delete_overlay_label(conn: sqlite3.Connection, repo: str) -> int:
    """Remove every row and tombstone the overlay holds under one label. Returns the file count."""
    paths = [
        row[0]
        for row in conn.execute(
            'SELECT file_path FROM indexed_file WHERE repo = ? UNION SELECT file_path FROM tombstone WHERE repo = ?', (repo, repo)
        )
    ]
    for path in paths:
        clear_overlay_file(conn, path)
    conn.commit()
    return len(paths)


def compact_overlay(conn: sqlite3.Connection, read_here: Collection[str] = ()) -> dict:
    """Drop every overlay row the base at DB_PATH holds the same content for, and record that base as reconciled.

    Under the labels in read_here, a row also goes when the base read its file at the same or
    a later mtime. That covers a save made while `indy watch` was down, which the run embedded
    and the overlay's older row would otherwise shadow. read_here names only labels this
    machine just indexed, where both mtimes come from this machine's files. Any other row may
    have been read from another machine's copy, so comparing its mtime could drop a save the
    base does not hold.

    A tombstone stays while the base still holds its path, and goes once a run has pruned it.
    Worktree rows always stay, because no full index covers a worktree.

    The stamp is taken before the base is attached. A base replaced in between is then the one
    compared, under the older stamp, and the next reconcile compares again rather than never.
    One write transaction, so a save landing mid-compaction is never deleted: `indy watch`
    writes the new content either before the comparison reads it or after the deletes commit.
    """
    stamp = base_stamp()
    conn.commit()
    if stamp is None:
        record_reconciled_stamp(conn, None)
        conn.commit()
        return {'files': 0, 'tombstones': 0}
    conn.execute('ATTACH DATABASE ? AS base', (file_uri(DB_PATH, 'ro'),))
    try:
        conn.execute('BEGIN IMMEDIATE')
        covered = [
            row[0]
            for row in conn.execute(
                """
                SELECT o.file_path FROM main.indexed_file o
                JOIN base.indexed_file b ON b.file_path = o.file_path
                WHERE o.branch IS NULL AND b.status = 'ok' AND (
                    b.content_hash = o.content_hash OR (b.repo IN (SELECT value FROM json_each(?)) AND b.mtime >= o.mtime)
                )
                """,
                (json.dumps(sorted(read_here)),),
            )
        ]
        for path in covered:
            clear_overlay_file(conn, path)
        buried = conn.execute('DELETE FROM main.tombstone WHERE file_path NOT IN (SELECT file_path FROM base.indexed_file)').rowcount
        record_reconciled_stamp(conn, stamp)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute('DETACH DATABASE base')
    return {'files': len(covered), 'tombstones': buried}


def get_overlay_summary(conn: sqlite3.Connection, scope: set[str] | None = None) -> dict | None:
    """How many files the attached overlay holds within scope, when it was last written, and whether reads apply it."""
    if not has_overlay(conn):
        return None
    labels = [row[0] for row in conn.execute('SELECT repo FROM overlay.indexed_file UNION ALL SELECT repo FROM overlay.tombstone')]
    updated = conn.execute('SELECT updated_at FROM overlay.overlay_state').fetchone()
    return {
        'files': sum(1 for label in labels if scope is None or label in scope),
        'updated_at': updated[0] if updated else None,
        'applied': overlay_applied(conn),
    }


# ── indexed_file ──────────────────────────────────────────────────────────────


def get_indexed_file(conn: sqlite3.Connection, file_path: str) -> dict | None:
    row = conn.execute('SELECT * FROM indexed_file WHERE file_path = ?', (file_path,)).fetchone()
    return dict(row) if row else None


def upsert_indexed_file(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT INTO indexed_file (repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status, extractor_version)
        VALUES (:repo, :file_path, :language, :mtime, :content_hash, :chunk_count, :indexed_at, :status, :extractor_version)
        ON CONFLICT (file_path) DO UPDATE SET
            repo         = excluded.repo,
            language     = excluded.language,
            mtime        = excluded.mtime,
            content_hash = excluded.content_hash,
            chunk_count  = excluded.chunk_count,
            indexed_at   = excluded.indexed_at,
            status       = excluded.status,
            extractor_version = excluded.extractor_version
        """,
        data,
    )


def set_extractor_version(conn: sqlite3.Connection, file_path: str, version: int) -> None:
    conn.execute('UPDATE indexed_file SET extractor_version = ? WHERE file_path = ?', (version, file_path))


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


def insert_chunk_embedding(conn: sqlite3.Connection, chunk_id: int, embedding: list[float] | bytes) -> None:
    """Store the float32 embedding for a chunk, or one already serialized. rowid must match chunk.id."""
    blob = embedding if isinstance(embedding, bytes) else sqlite_vec.serialize_float32(embedding)
    conn.execute('INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)', (chunk_id, blob))


def nearest_chunk_ids(conn: sqlite3.Connection, schema: str, query: bytes, limit: int) -> list[tuple[float, int]]:
    """KNN over one store's vectors, as (distance, chunk id), with an overlay id negated as its view presents it."""
    sign = -1 if schema == 'overlay' else 1
    rows = conn.execute(
        f'SELECT v.rowid, v.distance FROM {schema}.vec_chunks v WHERE v.embedding MATCH ? ORDER BY v.distance LIMIT ?',
        (query, limit),
    )
    return [(row['distance'], sign * row['rowid']) for row in rows]


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

    With an overlay attached, each store is searched on its own and the hits merged by
    distance. A vec0 MATCH needs the virtual table itself, so no view can merge them. A base
    hit in a file the overlay holds finds no row in the `chunk` view and is dropped there.
    """
    # Oversample to ensure enough results survive metadata filtering
    candidate_limit = min(limit * 20, 1000)
    query = sqlite_vec.serialize_float32(embedding)

    hits = nearest_chunk_ids(conn, 'main', query, candidate_limit)
    if overlay_applied(conn):
        hits = sorted(hits + nearest_chunk_ids(conn, 'overlay', query, candidate_limit))

    results = []
    for distance, chunk_id in hits:
        chunk = conn.execute('SELECT * FROM chunk WHERE id = ?', (chunk_id,)).fetchone()
        if chunk is None:
            continue
        if repo and chunk['repo'] != repo:
            continue
        if repos is not None and chunk['repo'] not in repos:
            continue
        if language and chunk['language'] != language:
            continue
        results.append({**dict(chunk), 'distance': distance})
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


def get_repo_file_paths(conn: sqlite3.Connection, repo: str) -> list[str]:
    """Every file path the manifest holds for a repo, whatever its status."""
    return [row[0] for row in conn.execute('SELECT file_path FROM indexed_file WHERE repo = ?', (repo,))]


def delete_indexed_file(conn: sqlite3.Connection, file_path: str) -> None:
    """Remove one manifest row. Its chunks and references are separate deletes."""
    conn.execute('DELETE FROM indexed_file WHERE file_path = ?', (file_path,))


def delete_repo(conn: sqlite3.Connection, repo: str) -> dict:
    """Remove every row a repo label owns. Returns what was deleted, per table.

    vec_chunks is a vec0 virtual table carrying no foreign key back to chunk, so its
    rows go by rowid gathered from the chunks first — the same walk delete_file_chunks
    does, for the same reason, and the reason the chunk rows cannot be deleted first.

    symbol_reference is keyed by source_file alone, so the manifest is what says which
    files belonged to the label. It is read before indexed_file is touched.

    index_run goes too. A label that survives in the run history is one `stats` still
    lists and `forget` did not forget, which would leave the verb's name a lie.
    """
    file_paths = [row[0] for row in conn.execute('SELECT file_path FROM indexed_file WHERE repo = ?', (repo,))]
    chunk_ids = [row[0] for row in conn.execute('SELECT id FROM chunk WHERE repo = ?', (repo,))]
    for chunk_id in chunk_ids:
        conn.execute('DELETE FROM vec_chunks WHERE rowid = ?', (chunk_id,))
    conn.execute('DELETE FROM chunk WHERE repo = ?', (repo,))
    for file_path in file_paths:
        conn.execute('DELETE FROM symbol_reference WHERE source_file = ?', (file_path,))
    files = conn.execute('DELETE FROM indexed_file WHERE repo = ?', (repo,)).rowcount
    runs = conn.execute('DELETE FROM index_run WHERE repo = ?', (repo,)).rowcount
    delete_scip_rows(conn, repo)
    conn.execute('DELETE FROM scip_index WHERE repo = ?', (repo,))
    conn.commit()
    return {'files': files, 'chunks': len(chunk_ids), 'runs': runs}


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


def get_last_scans(conn: sqlite3.Connection) -> dict[str, str]:
    """When each repo label was last scanned clean, as ISO timestamps.

    Taken from index_run rather than indexed_file.indexed_at, which dates something else: a
    file whose content hash is unchanged is skipped and keeps the timestamp of the run that
    embedded it, so the manifest says when a repo last *changed*. How far behind a read is
    depends on when the repo was last *looked at*, which is a run.

    Runs carrying an error are left out. One that blew up wrote nothing, so dating the index
    from the attempt would claim content the run never produced.
    """
    rows = conn.execute(
        """
        SELECT repo, MAX(finished_at) AS finished_at
        FROM index_run
        WHERE finished_at IS NOT NULL AND error IS NULL
        GROUP BY repo
        """
    ).fetchall()
    return {row['repo']: row['finished_at'] for row in rows}


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


VALUE_USE = "sr.ref_type IN ('call', 'reference')"


def imported_by_source_file(column: str) -> str:
    """A condition true when the row's own file imports the name in column."""
    return (
        'EXISTS (SELECT 1 FROM symbol_reference i'
        f" WHERE i.source_file = sr.source_file AND i.ref_type = 'import' AND i.target_symbol = {column})"
    )


def select_references(
    conn: sqlite3.Connection,
    condition: str,
    params: tuple,
    repo: str | None = None,
    order_by: str = 'sr.source_file, sr.source_symbol',
) -> list[dict]:
    """symbol_reference rows matching condition, narrowed to one repo through the manifest when repo is set."""
    join, repo_condition = ('JOIN indexed_file f ON sr.source_file = f.file_path', ' AND f.repo = ?') if repo else ('', '')
    rows = conn.execute(
        f'SELECT sr.* FROM symbol_reference sr {join} WHERE {condition}{repo_condition} ORDER BY {order_by}',
        (*params, repo) if repo else params,
    )
    return [dict(row) for row in rows]


def labeled(rows: list[dict], via: str | None = None) -> list[dict]:
    """Each row with how it matched: the named rule, or else its own ref_type."""
    return [{**row, 'via': via or row['ref_type']} for row in rows]


def get_symbol_callers(conn: sqlite3.Connection, target_symbol: str, repo: str | None = None) -> list[dict]:
    """Every row that calls target_symbol or uses it as a value."""
    return labeled(select_references(conn, f'sr.target_symbol = ? AND {VALUE_USE}', (target_symbol,), repo))


def get_class_lineage(conn: sqlite3.Connection, class_name: str) -> set[str]:
    """class_name and every class it inherits from, matched by name through the inherit rows.

    UNION rather than UNION ALL is what ends the walk on a cycle. Matching by name makes one
    possible: two indexed repos can each declare a class inheriting from the other's namesake.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE lineage(name) AS (
            VALUES (?)
            UNION
            SELECT sr.target_symbol
            FROM symbol_reference sr
            JOIN lineage ON sr.source_symbol = lineage.name
            WHERE sr.ref_type = 'inherit'
        )
        SELECT name FROM lineage
        """,
        (class_name,),
    )
    return {row[0] for row in rows}


def get_class_heirs(conn: sqlite3.Connection, class_name: str, member: str) -> set[str]:
    """Every class inheriting class_name's own `member`: its subclasses, down to any that defines one.

    An override is a different method, so a subclass defining `member` ends the walk below it.
    A definition is read from the chunk table, where each method is chunked as `Class.member`.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE heirs(name) AS (
            VALUES (?)
            UNION
            SELECT sr.source_symbol
            FROM symbol_reference sr
            JOIN heirs ON sr.target_symbol = heirs.name
            WHERE sr.ref_type = 'inherit'
              AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.symbol_name = sr.source_symbol || '.' || ?)
        )
        SELECT name FROM heirs
        """,
        (class_name, member),
    )
    return {row[0] for row in rows} - {class_name}


def is_indexed_class(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM chunk WHERE symbol_name = ? AND symbol_type = 'class' LIMIT 1", (name,)).fetchone() is not None


def get_member_callers(conn: sqlite3.Connection, owner: str, member: str, repo: str | None = None) -> list[dict]:
    """What reaches `owner.member`, by five syntactic rules, each row labeled with the rule that matched.

    - its ref_type: a row hinted with the owner's name, as `steps.apply(...)` is, which includes a
      parameter annotated with the owner itself;
    - `self`: `self.member` inside the owner, a class it inherits from, or a subclass that does
      not override `member`;
    - `typed`: `.member` on a parameter annotated with one of those classes other than the owner;
    - `module`: a bare `member` in a file named after the owner that does not import it;
    - `candidate`: `.member` called on a receiver nothing types. Listed after every resolved row,
      when the owner is an indexed class or no rule above matched anything.
    """
    name = owner.rpartition('.')[2]
    classes = get_class_lineage(conn, owner) | get_class_lineage(conn, name) | get_class_heirs(conn, name, member)
    related = sorted(classes - {owner, name})

    found = labeled(select_references(conn, f'sr.target_symbol = ? AND sr.target_module = ? AND {VALUE_USE}', (member, name), repo))
    self_uses = select_references(conn, "sr.target_symbol = ? AND sr.target_module IN ('self', 'cls')", (member,), repo)
    found += labeled([row for row in self_uses if any((row['source_symbol'] or '').startswith(f'{cls}.') for cls in classes)], 'self')
    if related:
        marks = ', '.join('?' * len(related))
        typed = select_references(conn, f'sr.target_symbol = ? AND sr.target_module IN ({marks}) AND {VALUE_USE}', (member, *related), repo)
        found += labeled(typed, 'typed')
    bare = select_references(
        conn,
        f'sr.target_symbol = ? AND sr.target_module IS NULL AND {VALUE_USE} AND NOT {imported_by_source_file("sr.target_symbol")}',
        (member,),
        repo,
    )
    found += labeled([row for row in bare if Path(row['source_file']).stem == name], 'module')
    if found and not is_indexed_class(conn, name):
        return found

    untyped = select_references(
        conn,
        "sr.target_symbol = ? AND sr.ref_type = 'call' AND sr.target_module IS NOT NULL AND sr.target_module NOT IN ('self', 'cls')"
        f' AND NOT {imported_by_source_file("sr.target_module")}',
        (member,),
        repo,
    )
    seen = {row['id'] for row in found}
    return found + labeled([row for row in untyped if row['id'] not in seen and not row['target_module'][:1].isupper()], 'candidate')


def get_symbol_callees(conn: sqlite3.Connection, source_symbol: str, repo: str | None = None, file_stem: str | None = None) -> list[dict]:
    """Every call made inside source_symbol, and inside each of its keys when it names a module-level dict.

    file_stem narrows the rows to files of that name, for a module function named `module.function`.
    """
    rows = select_references(
        conn,
        "(sr.source_symbol = ? OR substr(sr.source_symbol, 1, ?) = ?) AND sr.ref_type = 'call'",
        (source_symbol, len(source_symbol) + 1, f'{source_symbol}['),
        repo,
        order_by='sr.target_symbol',
    )
    return [row for row in rows if file_stem is None or Path(row['source_file']).stem == file_stem]


# ── scip ──────────────────────────────────────────────────────────────────────

SCIP_ROW_TABLES = ('scip_document', 'scip_symbol', 'scip_occurrence', 'scip_relationship')


def delete_scip_rows(conn: sqlite3.Connection, repo: str) -> None:
    for table in SCIP_ROW_TABLES:
        conn.execute(f'DELETE FROM {table} WHERE repo = ?', (repo,))


def get_python_file_hashes(conn: sqlite3.Connection, repo: str) -> list[tuple[str, str]]:
    """(file_path, content_hash) of every Python file indexed under repo, in path order."""
    rows = conn.execute(
        "SELECT file_path, content_hash FROM indexed_file WHERE repo = ? AND language = 'python' AND status = 'ok' ORDER BY file_path",
        (repo,),
    )
    return [(row[0], row[1]) for row in rows]


def get_scip_state(conn: sqlite3.Connection, repo: str) -> dict | None:
    row = conn.execute('SELECT * FROM scip_index WHERE repo = ?', (repo,)).fetchone()
    return dict(row) if row else None


def get_scip_states(conn: sqlite3.Connection) -> list[dict]:
    if not has_scip_tables(conn):
        return []
    return [dict(row) for row in conn.execute('SELECT * FROM scip_index ORDER BY repo')]


def replace_scip_index(conn: sqlite3.Connection, state: dict, rows: dict[str, list[dict]] | None = None) -> None:
    """Record what the last scip-python pass over a repo did, with the rows it produced, in one transaction.

    rows of None leaves the repo with none, which is what a skip or a failure leaves: `deps`
    then reads the repo's syntactic rows rather than links into code that has since changed.
    """
    rows = rows or {}
    try:
        delete_scip_rows(conn, state['repo'])
        for table in SCIP_ROW_TABLES:
            batch = rows.get(table, [])
            if batch:
                columns = list(batch[0])
                conn.executemany(
                    f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({", ".join(":" + column for column in columns)})',
                    batch,
                )
        conn.execute(
            """
            INSERT INTO scip_index (repo, indexed_at, status, reason, fingerprint, document_count, reference_count, seconds)
            VALUES (:repo, :indexed_at, :status, :reason, :fingerprint, :document_count, :reference_count, :seconds)
            ON CONFLICT (repo) DO UPDATE SET
                indexed_at = excluded.indexed_at, status = excluded.status, reason = excluded.reason,
                fingerprint = excluded.fingerprint, document_count = excluded.document_count,
                reference_count = excluded.reference_count, seconds = excluded.seconds
            """,
            state,
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def get_scip_targets(conn: sqlite3.Connection, owner: str, member: str) -> list[dict]:
    """The symbols scip-python defines that `owner.member`, or a bare `member` when owner is empty, can name.

    An owner names a class by its last name, `Provider` for `Provider.install_all` and for
    `Outer.Provider.install_all`, or a module by its last component, `registry` for
    `pkg.registry.install_all`.
    """
    if not has_scip_tables(conn):
        return []
    rows = [dict(row) for row in conn.execute('SELECT DISTINCT repo, symbol, module, name FROM scip_symbol WHERE member = ?', (member,))]
    if not owner:
        return rows
    name = owner.rpartition('.')[2]
    qualified = f'{name}.{member}'
    return [
        row
        for row in rows
        if row['name'] == qualified
        or row['name'].endswith(f'.{qualified}')
        or (row['name'] == member and row['module'].rpartition('.')[2] == name)
    ]


def get_scip_overridden(conn: sqlite3.Connection, symbols: set[str]) -> set[str]:
    """Every method the given ones override, up the whole chain, from scip-python's implementation links."""
    found: set[str] = set()
    frontier = set(symbols)
    while frontier:
        marks = ', '.join('?' * len(frontier))
        rows = conn.execute(
            f"SELECT DISTINCT target_symbol FROM scip_relationship WHERE kind = 'implementation' AND symbol IN ({marks})", tuple(frontier)
        )
        frontier = {row[0] for row in rows} - found - symbols
        found |= frontier
    return found


def get_scip_references(conn: sqlite3.Connection, symbols: set[str], repo: str | None = None) -> list[dict]:
    """Every value use of the given symbols, with the caller it sits in, narrowed to one repo when repo is set."""
    if not symbols:
        return []
    marks = ', '.join('?' * len(symbols))
    repo_condition = ' AND repo = ?' if repo else ''
    rows = conn.execute(
        f'SELECT * FROM scip_occurrence WHERE symbol IN ({marks}){repo_condition} ORDER BY file_path, line',
        (*symbols, repo) if repo else tuple(symbols),
    )
    return [dict(row) for row in rows]


def get_scip_documents(conn: sqlite3.Connection, repos: set[str]) -> dict[str, str]:
    """Each file scip-python linked in the given repos, less any the overlay holds, mapped to its repo."""
    marks = ', '.join('?' * len(repos))
    return {row[0]: row[1] for row in conn.execute(f'SELECT file_path, repo FROM scip_document WHERE repo IN ({marks})', tuple(repos))}


def get_scip_call_sites(conn: sqlite3.Connection, member: str) -> set[tuple[str, str | None]]:
    """(file_path, source_symbol) of every place scip-python resolved a use of anything named member."""
    rows = conn.execute('SELECT DISTINCT file_path, source_symbol FROM scip_occurrence WHERE member = ?', (member,))
    return {(row[0], row[1]) for row in rows}


def get_scip_call_targets(conn: sqlite3.Connection, file_path: str, source_symbol: str | None, member: str) -> set[str]:
    """Every symbol scip-python resolved a call of member to, inside one caller."""
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM scip_occurrence WHERE file_path = ? AND source_symbol IS ? AND member = ? AND ref_type = 'call'",
        (file_path, source_symbol, member),
    )
    return {row[0] for row in rows}
