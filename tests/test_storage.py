"""Guards on the two properties that make the index safe to replicate.

Both failed silently before: searching wrote to the database, and a run rewrote the file in
place while a syncer was reading it. Neither reported anything — the index just decayed.
"""

import sqlite3

import pytest

from indy import service
from indy import storage

INSERT_FILE = (
    'INSERT INTO indexed_file (repo, file_path, language, mtime, content_hash, chunk_count, indexed_at, status)'
    " VALUES ('r', ?, NULL, 1.0, 'h', 1, '2026-01-01', ?)"
)


def add_file(conn, path, status='ok'):
    conn.execute(INSERT_FILE, (path, status))


@pytest.fixture
def indy_dir(tmp_path, monkeypatch):
    """Point both modules at a temporary index, and return its directory."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')
    return tmp_path


@pytest.fixture
def existing_index(indy_dir):
    """An index with one file already in it."""
    conn = storage.open_working_db()
    add_file(conn, '~/r/a.py')
    storage.commit_working_db(conn)
    return indy_dir


def test_a_run_never_writes_the_index_in_place(existing_index):
    """The file a syncer carries must not change until the run is finished."""
    before = (existing_index / 'indy.db').read_bytes()

    conn = storage.open_working_db()
    add_file(conn, '~/r/b.py')
    conn.commit()
    assert (existing_index / 'indy.db').read_bytes() == before

    storage.commit_working_db(conn)
    assert (existing_index / 'indy.db').read_bytes() != before


def test_a_finished_index_stands_alone(existing_index):
    """Delete journal mode, no sidecars: a copy of this one file is the whole index."""
    assert sorted(p.name for p in existing_index.iterdir()) == ['indy.db']

    check = sqlite3.connect(f'file:{existing_index / "indy.db"}?mode=ro', uri=True)
    assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert check.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    check.close()


def test_a_read_connection_cannot_write(existing_index):
    """The passive checkpoint on close is a write, and it is what made peers diverge."""
    conn = storage.get_read_db()
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        add_file(conn, '~/r/b.py')
    conn.close()


def test_reading_leaves_the_index_untouched(existing_index):
    """A replicated index must come back byte-identical after being searched."""
    before = (existing_index / 'indy.db').read_bytes()

    conn = storage.get_read_db()
    assert conn.execute('SELECT COUNT(*) FROM indexed_file').fetchone()[0] == 1
    conn.close()

    assert (existing_index / 'indy.db').read_bytes() == before
    assert sorted(p.name for p in existing_index.iterdir()) == ['indy.db']


def test_an_interrupted_run_keeps_what_it_embedded(existing_index):
    """Ctrl-C is how a multi-hour index gets stopped, not a failure.

    Discarding the working copy on interrupt loses the whole run — including its index_run
    rows, so it leaves no trace it ever happened, and the next run offers to re-embed
    everything. Every file is committed as it goes, so the copy is a complete database and
    swapping it in is the correct answer.
    """
    with pytest.raises(KeyboardInterrupt), service.index_session() as session:
        add_file(session._conn, '~/r/b.py')
        session._conn.commit()
        raise KeyboardInterrupt

    read = storage.get_read_db()
    paths = {row['file_path'] for row in read.execute('SELECT file_path FROM indexed_file')}
    read.close()
    assert paths == {'~/r/a.py', '~/r/b.py'}
    assert sorted(p.name for p in existing_index.iterdir()) == ['indy.db']


def test_a_failed_run_leaves_the_index_as_it_was(existing_index):
    before = (existing_index / 'indy.db').read_bytes()

    with pytest.raises(RuntimeError), service.index_session():
        raise RuntimeError('embedding failed')

    assert (existing_index / 'indy.db').read_bytes() == before
    assert sorted(p.name for p in existing_index.iterdir()) == ['indy.db']


def test_a_killed_run_does_not_poison_the_next_one(existing_index):
    """A working copy left by a killed run is rebuilt from the index, not resumed."""
    stale = existing_index / 'indy.db.building'
    stale.write_bytes(b'not a database at all')

    conn = storage.open_working_db()
    assert conn.execute('SELECT COUNT(*) FROM indexed_file').fetchone()[0] == 1
    storage.commit_working_db(conn)


def test_the_working_copy_carries_the_previous_index_forward(existing_index):
    """Incremental indexing depends on the manifest surviving the copy."""
    conn = storage.open_working_db()
    add_file(conn, '~/r/b.py')
    storage.commit_working_db(conn)

    read = storage.get_read_db()
    assert read.execute('SELECT COUNT(*) FROM indexed_file').fetchone()[0] == 2
    read.close()


def test_upgrading_from_a_wal_mode_index_leaves_no_sidecars(indy_dir):
    """The state every machine is in before the first run of this version.

    A -wal is read as the live tail of whatever database sits beside it. While it belongs to
    that database that is correct and the seed copy must read through it — but once the
    finished copy is renamed over the top, the same file describes a database that no longer
    exists, and a search answers out of it with no error anywhere.
    """
    legacy = sqlite3.connect(indy_dir / 'indy.db')
    legacy.execute('PRAGMA journal_mode=WAL')
    legacy.execute(
        'CREATE TABLE indexed_file (id INTEGER PRIMARY KEY, repo TEXT, file_path TEXT UNIQUE,'
        ' language TEXT, mtime REAL, content_hash TEXT, chunk_count INTEGER, indexed_at TEXT, status TEXT)'
    )
    add_file(legacy, '~/r/a.py')
    legacy.commit()
    # Left open on purpose: closing the last connection checkpoints the WAL away, and the
    # sidecars still being there is the state under test.
    assert (indy_dir / 'indy.db-wal').exists()

    conn = storage.open_working_db()
    add_file(conn, '~/r/b.py')
    storage.commit_working_db(conn)

    assert sorted(p.name for p in indy_dir.iterdir()) == ['indy.db']

    read = storage.get_read_db()
    paths = {row['file_path'] for row in read.execute('SELECT file_path FROM indexed_file')}
    read.close()
    assert paths == {'~/r/a.py', '~/r/b.py'}


def test_a_first_run_builds_an_index_from_nothing(indy_dir):
    conn = storage.open_working_db()
    add_file(conn, '~/r/a.py')
    storage.commit_working_db(conn)
    assert (indy_dir / 'indy.db').exists()


def test_a_read_never_creates_a_database(indy_dir):
    """An empty database conjured by a search would answer every query with nothing, and on
    a machine waiting for a peer's copy it would be a second file for the syncer to fight."""
    with pytest.raises(storage.IndexNotFoundError):
        storage.get_read_db()
    assert not (indy_dir / 'indy.db').exists()


def test_clearing_errors_swaps_a_new_index_in(existing_index):
    conn = storage.open_working_db()
    add_file(conn, '~/r/bad.py', status='error: boom')
    storage.commit_working_db(conn)

    assert service.clear_errors() == 1

    read = storage.get_read_db()
    assert read.execute("SELECT COUNT(*) FROM indexed_file WHERE status != 'ok'").fetchone()[0] == 0
    read.close()
    assert sorted(p.name for p in existing_index.iterdir()) == ['indy.db']
