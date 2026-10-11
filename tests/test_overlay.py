"""A save reaches every read through the overlay, and a full index takes back what it covers.

The base is built by a real `index_path` with a deterministic embedding in place of ollama.
Each test then edits files on disk and applies them with `update_overlay`, the step `indy watch`
runs per saved file, so what is checked is what a read returns afterwards.
"""

import hashlib
import shutil

import pytest

from indy import service
from indy import storage
from indy.main import describe_freshness
from indy.watch import WorkQueue

TOOLS = """\
def load_rows():
    return query()


def render(rows):
    return format_rows(rows)
"""

TOOLS_EDITED = """\
def load_rows():
    return query()


def paint(rows):
    return draw_rows(rows)
"""


def fake_embedding(text: str) -> list[float]:
    return [byte / 255 for byte in hashlib.sha256(text.encode()).digest()] * 24


@pytest.fixture
def embedded(monkeypatch):
    """Every text sent to the embedding model, in order."""
    calls: list[str] = []

    def embed(text: str) -> list[float]:
        calls.append(text)
        return fake_embedding(text)

    monkeypatch.setattr(service, 'embed_text', embed)
    return calls


@pytest.fixture
def repo(tmp_path, monkeypatch, embedded):
    """A repo labeled `r`, indexed into a base: tools.py, and gone.py for deleting."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'tools.py').write_text(TOOLS)
    (root / 'gone.py').write_text('def doomed():\n    return 1\n')
    service.index_path(root, 'r')
    embedded.clear()
    return root


@pytest.fixture
def overlay(repo):
    """The overlay, reconciled with the base as `indy watch` reconciles it on starting."""
    conn = storage.open_overlay_db()
    storage.compact_overlay(conn)
    yield conn
    conn.close()


def build_base_elsewhere(monkeypatch, root, directory):
    """Index root into a base under directory, the way another machine or another data_dir would, and return its path."""
    directory.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'DB_PATH', directory / 'indy.db')
        patch.setattr(storage, 'WORKING_DB_PATH', directory / 'indy.db.building')
        service.index_path(root, 'r')
    return directory / 'indy.db'


@pytest.fixture
def worktree(repo, tmp_path):
    """A linked worktree of repo on branch `feature`, identical to the main checkout."""
    path = tmp_path / 'worktree'
    shutil.copytree(repo, path)
    return path


def save(conn, root: service.LiveRoot, name: str, content: str | None) -> service.LiveChange | None:
    """Write a file under root, or delete it when content is None, and apply it as `indy watch` would."""
    path = root.path / name
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    return service.update_overlay(conn, path, root)


def symbols(name: str) -> list[tuple[str, str | None]]:
    return sorted((r['file_path'], r['branch']) for r in service.search_symbol(name))


def callers(name: str) -> list[str]:
    return [c['source_symbol'] for c in service.get_dependencies(name, direction='callers')['callers']]


def test_a_save_replaces_what_search_symbol_and_deps_return_for_that_file(repo, overlay):
    change = save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED)

    assert change is not None and change.action == 'indexed'
    assert symbols('render') == []
    assert symbols('paint') == [(str(repo / 'tools.py'), None)]
    assert callers('draw_rows') == ['paint']
    assert callers('format_rows') == []
    texts = [hit['text'] for hit in service.search('paint the rows', limit=10)]
    assert any('def paint' in text for text in texts)
    assert not any('def render' in text for text in texts)


def test_a_save_embeds_only_the_chunks_whose_text_changed(repo, overlay, embedded):
    change = save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED)

    assert change is not None
    assert (change.embedded, change.reused) == (1, 1)
    assert len(embedded) == 1 and 'def paint' in embedded[0]


def test_a_file_saved_back_to_its_indexed_content_leaves_the_overlay(repo, overlay):
    root = service.LiveRoot(repo, 'r')
    save(overlay, root, 'tools.py', TOOLS_EDITED)
    change = save(overlay, root, 'tools.py', TOOLS)

    assert change is not None and change.action == 'restored'
    assert storage.get_overlay_paths(overlay, '') == []
    assert symbols('render') == [(str(repo / 'tools.py'), None)]


def test_a_deleted_file_drops_out_of_every_read(repo, overlay):
    change = save(overlay, service.LiveRoot(repo, 'r'), 'gone.py', None)

    assert change is not None and change.action == 'removed'
    assert symbols('doomed') == []
    assert not any(hit['file_path'].endswith('gone.py') for hit in service.search('doomed', limit=10))


def test_a_worktree_file_indexes_under_the_repo_label_with_its_branch_only_where_it_differs(repo, overlay, worktree, embedded):
    root = service.LiveRoot(worktree, 'r', branch='feature', checkout=repo)

    assert service.update_overlay(overlay, worktree / 'tools.py', root) is None
    change = save(overlay, root, 'tools.py', TOOLS_EDITED)

    assert change is not None and change.embedded == 1
    assert symbols('paint') == [(str(worktree / 'tools.py'), 'feature')]
    assert symbols('render') == [(str(repo / 'tools.py'), None)]
    assert {r['repo'] for r in service.search_symbol('paint')} == {'r'}


def test_a_full_index_empties_the_overlay_of_every_file_it_covered(repo, overlay, worktree):
    main = service.LiveRoot(repo, 'r')
    save(overlay, main, 'tools.py', TOOLS_EDITED)
    save(overlay, main, 'gone.py', None)
    save(overlay, service.LiveRoot(worktree, 'r', branch='feature', checkout=repo), 'tools.py', TOOLS_EDITED.replace('paint', 'sketch'))

    service.index_path(repo, 'r')

    assert storage.get_overlay_paths(overlay, '') == [str(worktree / 'tools.py')]
    assert symbols('paint') == [(str(repo / 'tools.py'), None)]
    assert symbols('doomed') == []


def test_a_full_index_that_read_a_newer_save_takes_the_file_back_from_the_overlay(repo, overlay):
    """A save made while nothing watched: the run embeds it, so the overlay's older row must not shadow it."""
    save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED)
    (repo / 'tools.py').write_text(TOOLS_EDITED.replace('paint', 'sketch'))

    service.index_path(repo, 'r')

    assert storage.get_overlay_paths(overlay, '') == []
    assert symbols('sketch') == [(str(repo / 'tools.py'), None)]


def test_a_base_a_syncer_delivers_into_data_dir_sets_aside_an_overlay_reconciled_with_the_last_one(repo, overlay, monkeypatch, tmp_path):
    """The receiving machine's watcher is stopped, so nothing has compared its saves with the base that arrived."""
    save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED.replace('paint', 'sketch'))
    (repo / 'tools.py').write_text(TOOLS_EDITED)
    delivered = build_base_elsewhere(monkeypatch, repo, tmp_path / 'peer')

    delivered.replace(storage.DB_PATH)

    assert symbols('sketch') == []
    assert symbols('paint') == [(str(repo / 'tools.py'), None)]
    freshness = service.index_freshness()
    assert freshness is not None
    assert describe_freshness(freshness).endswith('Live: 1 saved file set aside until `indy watch` reconciles the overlay with this index.')


def test_a_read_or_an_index_under_another_data_dir_never_touches_this_index_overlay(repo, overlay, monkeypatch, tmp_path):
    save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED)
    (repo / 'tools.py').write_text(TOOLS)
    other = build_base_elsewhere(monkeypatch, repo, tmp_path / 'other')

    with monkeypatch.context() as patch:
        patch.setattr(storage, 'DB_PATH', other)
        assert symbols('paint') == []
        assert service.index_freshness()['overlay'] is None

    assert storage.get_overlay_paths(overlay, '') == [str(repo / 'tools.py')]
    assert symbols('paint') == [(str(repo / 'tools.py'), None)]


def test_every_read_states_how_many_files_the_overlay_holds_beyond_the_scan(repo, overlay):
    save(overlay, service.LiveRoot(repo, 'r'), 'tools.py', TOOLS_EDITED)

    freshness = service.index_freshness()

    assert freshness is not None
    assert freshness['overlay']['files'] == 1
    assert describe_freshness(freshness).endswith('Live: 1 file newer than the scan, updated under a minute ago.')


def test_a_save_jumps_the_queue_ahead_of_the_catch_up_backlog(tmp_path):
    queue = WorkQueue()
    queue.push_oldest([tmp_path / 'backlog-1', tmp_path / 'backlog-2'])
    queue.push_newest([tmp_path / 'saved'])
    queue.push_oldest([tmp_path / 'saved'])

    assert [queue.pop(timeout=0) for _ in range(4)] == [tmp_path / 'saved', tmp_path / 'backlog-1', tmp_path / 'backlog-2', None]
