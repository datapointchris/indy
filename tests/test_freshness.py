"""Every read states how far behind the index is, so a miss cannot be read as an absence.

A flat "no symbol named X" is the same sentence whether X does not exist or was written
after the last scan, and the second is the common case: indexing is manual and a repo goes
days between runs. The guarantee here is that the horizon is always on the page — on a miss,
on a hit, and whether the index is minutes or months old.
"""

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from indy import service
from indy import storage
from indy.main import indy_app

runner = CliRunner()

INSERT_CHUNK = (
    'INSERT INTO chunk (file_path, repo, language, symbol_name, symbol_type, module, text)'
    " VALUES (?, ?, 'python', ?, 'function', 'm', 'body')"
)


def ago(**delta) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat()


def add_run(conn, repo: str, finished_at: str, error: str | None = None) -> None:
    run_id = storage.start_index_run(conn, repo, finished_at)
    storage.finish_index_run(
        conn,
        run_id,
        {'finished_at': finished_at, 'files_scanned': 1, 'files_updated': 0, 'chunks_added': 0, 'error': error},
    )


@pytest.fixture
def index(tmp_path, monkeypatch):
    """An index holding one symbol, scanned 15 days ago in one repo and an hour ago in another."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')

    conn = storage.open_working_db()
    conn.execute(INSERT_CHUNK, ('~/stale/a.py', 'stale', 'outranks'))
    add_run(conn, 'stale', ago(days=15))
    add_run(conn, 'fresh', ago(hours=1))
    storage.commit_working_db(conn)
    return tmp_path


def test_the_oldest_scan_in_scope_is_the_one_reported():
    """The newest describes the last repo someone indexed and nothing about the rest of them."""
    summary = service.summarize_freshness({'stale': ago(days=15), 'fresh': ago(minutes=2)})

    assert summary['repos'] == 2
    assert summary['oldest']['repo'] == 'stale'
    assert summary['newest']['repo'] == 'fresh'
    assert summary['oldest']['age_seconds'] > summary['newest']['age_seconds']


def test_a_scope_measures_only_the_repos_the_read_could_match():
    """`--repo fresh` cannot return a stale repo's rows, so it must not be charged its age."""
    summary = service.summarize_freshness({'stale': ago(days=15), 'fresh': ago(minutes=2)}, scope={'fresh'})

    assert summary['repos'] == 1
    assert summary['oldest']['repo'] == 'fresh'


def test_nothing_scanned_reports_nothing():
    """A fresh install has no horizon to state, and inventing one would be the same lie."""
    assert service.summarize_freshness({}) is None


def test_an_errored_run_does_not_date_the_index(tmp_path, monkeypatch):
    """A run that blew up wrote nothing, so it cannot be what the content is dated from."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')

    conn = storage.open_working_db()
    clean = ago(days=10)
    add_run(conn, 'r', clean)
    add_run(conn, 'r', ago(minutes=1), error='ollama refused the connection')
    storage.commit_working_db(conn)

    read = storage.get_read_db()
    try:
        assert storage.get_last_scans(read) == {'r': clean}
    finally:
        read.close()


def test_a_symbol_miss_says_how_old_the_index_is(index):
    """The reported failure: a symbol written after the last scan answered as if absent."""
    result = runner.invoke(indy_app, ['symbol', 'written_today'])

    assert result.exit_code == 0
    assert 'No symbol named' in result.output
    assert '15 days ago' in result.output


def test_a_hit_says_how_old_the_index_is_too(index):
    """A stale hit is the worse half: it names a file the symbol has since moved out of."""
    result = runner.invoke(indy_app, ['symbol', 'outranks'])

    assert result.exit_code == 0
    assert 'outranks' in result.output
    assert '15 days ago' in result.output


def test_deps_says_how_old_the_index_is(index):
    """The command the miss was found through, which reads the same graph the scan wrote."""
    result = runner.invoke(indy_app, ['deps', 'written_today'])

    assert result.exit_code == 0
    assert '15 days ago' in result.output


def test_a_scoped_read_is_dated_by_that_repo_alone(index):
    """One repo in scope, so the sentence names it rather than a range across two."""
    result = runner.invoke(indy_app, ['symbol', 'outranks', '--repo', 'fresh'])

    assert result.exit_code == 0
    assert 'fresh scanned 1 hour ago' in result.output


def test_json_carries_the_freshness_an_agent_would_otherwise_have_to_ask_for(index):
    """Agents shell out with --json and never see the printed caveat."""
    result = runner.invoke(indy_app, ['symbol', 'written_today', '--json'])

    payload = json.loads(result.stdout)
    assert payload['results'] == []
    assert payload['freshness']['oldest']['repo'] == 'stale'
    assert payload['freshness']['oldest']['age_seconds'] > 14 * 86400
