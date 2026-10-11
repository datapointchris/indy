"""`indy watch` over a real git repo: a save reaches a read, a deletion leaves it, and a worktree comes and goes.

The watcher runs in a thread exactly as the command runs it, with real inotify events. Only the
embedding model is replaced.
"""

import subprocess
import threading
import time
from collections.abc import Callable

import pytest

from indy import service
from indy import storage
from indy.repos import OWNED
from indy.repos import IndexTarget

from .test_overlay import fake_embedding


def git(cwd, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True, capture_output=True)


def eventually(check: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError('not reached within the timeout')
        time.sleep(0.05)


def symbols(name: str) -> list[tuple[str, str | None]]:
    return sorted((r['file_path'], r['branch']) for r in service.search_symbol(name))


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
    (root / '.gitignore').write_text('scratch.py\n')
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '.')
    git(root, 'commit', '-q', '-m', 'start')
    monkeypatch.setattr(service, 'all_index_targets', lambda: [IndexTarget(name='r', path=root, kind=OWNED)])
    service.index_path(root, 'r')
    return root


@pytest.fixture
def watching(repo):
    """`indy watch` running in a thread, and every problem it reported."""
    stop = threading.Event()
    problems: list[str] = []
    thread = threading.Thread(target=service.watch, args=(stop, lambda _change: None, lambda _note: None, problems.append))
    thread.start()
    time.sleep(1.0)
    yield problems
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()


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


def test_a_worktree_indexes_under_its_branch_and_its_rows_go_with_it(repo, watching, tmp_path):
    tree = tmp_path / 'tree'
    git(repo, 'worktree', 'add', '-q', '-b', 'feature', str(tree))
    time.sleep(1.0)

    (tree / 'tools.py').write_text('def render():\n    return 5\n')
    eventually(lambda: symbols('render') == [(str(repo / 'tools.py'), None), (str(tree / 'tools.py'), 'feature')])

    git(repo, 'worktree', 'remove', '--force', str(tree))
    eventually(lambda: symbols('render') == [(str(repo / 'tools.py'), None)])
    assert watching == []
