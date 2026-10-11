"""`indy watch` over a real git repo: a save reaches a read, a deletion leaves it, and a worktree comes and goes.

The watcher runs in a thread exactly as the command runs it, with real inotify events. Only the
embedding model is replaced.
"""

import subprocess
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import closing
from contextlib import contextmanager

import pytest

from indy import service
from indy import storage
from indy.repos import OWNED
from indy.repos import IndexTarget

from .test_overlay import build_base_elsewhere
from .test_overlay import callers
from .test_overlay import fake_embedding
from .test_overlay import symbols


def git(cwd, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True, capture_output=True)


def eventually(check: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError('not reached within the timeout')
        time.sleep(0.05)


def live_files() -> int:
    freshness = service.index_freshness()
    assert freshness is not None and freshness['overlay'] is not None
    return freshness['overlay']['files']


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A committed git repo registered as the one owned target, indexed into a base."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')
    monkeypatch.setattr(service, 'embed_text', fake_embedding)
    monkeypatch.setattr(service, 'warm_model', lambda: None)
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'tools.py').write_text('def render():\n    return 1\n')
    (root / 'gone.py').write_text('def doomed():\n    return 1\n')
    (root / '.gitignore').write_text('scratch.py\n')
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '.')
    git(root, 'commit', '-q', '-m', 'start')
    monkeypatch.setattr(service, 'all_index_targets', lambda: [IndexTarget(name='r', path=root, kind=OWNED)])
    service.index_path(root, 'r')
    return root


@contextmanager
def running_watch() -> Iterator[list[str]]:
    """`indy watch` running in a thread for the duration, and every problem it reported."""
    stop = threading.Event()
    problems: list[str] = []
    thread = threading.Thread(target=service.watch, args=(stop, lambda _change: None, lambda _note: None, problems.append))
    thread.start()
    try:
        yield problems
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()


@pytest.fixture
def watching(repo):
    with running_watch() as problems:
        time.sleep(1.0)
        yield problems


def test_a_save_is_searchable_and_a_deletion_is_gone_while_watching(repo, watching):
    (repo / 'tools.py').write_text('def paint():\n    return 2\n')
    eventually(lambda: symbols('paint') == [(str(repo / 'tools.py'), None)])
    assert symbols('render') == []

    (repo / 'tools.py').unlink()
    eventually(lambda: symbols('paint') == [])

    (repo / 'scratch.py').write_text('def ignored():\n    return 3\n')
    (repo / 'kept.py').write_text('def kept():\n    return 4\n')
    eventually(lambda: symbols('kept') != [])
    assert symbols('ignored') == []
    assert watching == []


def test_a_watch_starting_late_indexes_what_changed_while_nothing_watched(repo):
    """Restarting, so the overlay is already reconciled with the base and only the restart itself can catch up."""
    with closing(storage.open_overlay_db()) as overlay:
        storage.compact_overlay(overlay)
    (repo / 'tools.py').write_text('def paint():\n    return 2\n')
    (repo / 'gone.py').unlink()
    (repo / 'new.py').write_text('def fresh():\n    return 3\n')

    with running_watch() as problems:
        eventually(lambda: symbols('paint') != [] and symbols('fresh') != [] and symbols('doomed') == [])

    assert symbols('paint') == [(str(repo / 'tools.py'), None)]
    assert symbols('fresh') == [(str(repo / 'new.py'), None)]
    assert problems == []


def test_a_worktree_indexes_under_its_branch_and_its_rows_go_with_it(repo, watching, tmp_path):
    tree = tmp_path / 'tree'
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(tree))
    time.sleep(1.0)

    (tree / 'tools.py').write_text('def render():\n    return 5\n')
    eventually(lambda: symbols('render') == [(str(repo / 'tools.py'), None), (str(tree / 'tools.py'), 'feature')])

    git(repo, 'worktree', 'remove', '--force', str(tree))
    eventually(lambda: symbols('render') == [(str(repo / 'tools.py'), None)])
    assert watching == []


def test_an_edit_or_checkout_in_the_main_checkout_while_a_worktree_exists_rechecks_the_worktree_copy(repo, watching, tmp_path):
    """The worktree's copy is held only while it differs from the main checkout's, so a change on either side decides it."""
    tree = tmp_path / 'tree'
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(tree))
    time.sleep(1.0)

    (repo / 'tools.py').write_text('def paint():\n    return 2\n')
    eventually(lambda: symbols('render') == [(str(tree / 'tools.py'), 'feature')])
    assert symbols('paint') == [(str(repo / 'tools.py'), None)]

    git(repo, 'checkout', '--', 'tools.py')
    eventually(lambda: symbols('render') == [(str(repo / 'tools.py'), None)])
    assert watching == []


def test_a_save_while_a_worktree_add_restarts_the_watch_is_still_indexed(repo, tmp_path, monkeypatch):
    """The save lands after the watch handed over the worktree's event and before it watches again, so no event reports it."""
    listed = service.live_roots
    saved = threading.Event()

    def list_then_save() -> list[service.LiveRoot]:
        roots = listed()
        if len(roots) > 1 and not saved.is_set():
            (repo / 'tools.py').write_text('def paint():\n    return 2\n')
            saved.set()
        return roots

    monkeypatch.setattr(service, 'live_roots', list_then_save)
    with running_watch() as problems:
        time.sleep(1.0)
        git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(tmp_path / 'tree'))
        eventually(saved.is_set)
        eventually(lambda: symbols('paint') == [(str(repo / 'tools.py'), None)])
    assert problems == []


def test_a_watch_where_a_syncer_delivers_the_index_into_data_dir_takes_back_what_the_delivered_index_holds(repo, monkeypatch, tmp_path):
    """Only the machine that built an index compacts after building it, so a watch must notice one arriving."""
    (repo / 'tools.py').write_text('def paint():\n    return 2\n')
    delivered = build_base_elsewhere(monkeypatch, repo, tmp_path / 'peer')

    with running_watch() as problems:
        eventually(lambda: symbols('paint') == [(str(repo / 'tools.py'), None)])
        assert live_files() == 1

        delivered.replace(storage.DB_PATH)
        eventually(lambda: live_files() == 0)

    assert symbols('paint') == [(str(repo / 'tools.py'), None)]
    assert problems == []


def test_a_watch_started_after_an_extractor_change_re_extracts_the_files_its_overlay_holds(repo, monkeypatch):
    with closing(storage.open_overlay_db()) as overlay, monkeypatch.context() as patch:
        storage.compact_overlay(overlay)
        patch.setattr(service, 'EXTRACTOR_VERSION', service.EXTRACTOR_VERSION - 1)
        patch.setattr(service, 'extract_references', lambda _path, _content: [])
        (repo / 'tools.py').write_text('def paint():\n    return draw()\n')
        service.update_overlay(overlay, repo / 'tools.py', service.LiveRoot(repo, 'r'))
    assert callers('draw') == []

    with running_watch() as problems:
        eventually(lambda: callers('draw') == ['paint'])
    assert problems == []
