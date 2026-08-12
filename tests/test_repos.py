import json

import pytest

from indy import repos
from indy.repos import IndexTarget
from indy.repos import is_excluded


def write_registries(tmp_path, monkeypatch, portfolio, exemplars):
    repos_file = tmp_path / 'repos.json'
    exemplar_file = tmp_path / 'exemplar-repos.json'
    repos_file.write_text(json.dumps({'owner': 'test-owner', 'repos': portfolio}))
    exemplar_file.write_text(json.dumps({'repos': exemplars}))
    monkeypatch.setattr(repos, 'REPOS_FILE', repos_file)
    monkeypatch.setattr(repos, 'EXEMPLAR_REPOS_FILE', exemplar_file)


def test_same_name_in_both_registries_stays_distinct(tmp_path, monkeypatch):
    """A repo you own and an exemplar clone can share a bare name. Indexing them under
    one label merged their chunks and put the name in both the owned and exemplar sets."""
    mine = tmp_path / 'homelab'
    theirs = tmp_path / 'refs' / 'homelab'
    mine.mkdir()
    theirs.mkdir(parents=True)

    write_registries(
        tmp_path,
        monkeypatch,
        portfolio=[{'name': 'homelab', 'path': str(mine), 'status': 'active'}],
        exemplars=[{'name': 'homelab', 'path': str(theirs), 'owner': 'upstream'}],
    )

    owned = repos.get_owned_repo_names()
    exemplar = repos.get_exemplar_repo_names()

    assert owned == {'homelab'}
    assert exemplar == {'upstream/homelab'}
    assert not (owned & exemplar), 'a name must never land in both sets'

    assert repos.get_target_by_name('homelab').path == mine
    assert repos.get_target_by_name('upstream/homelab').path == theirs


def test_dormant_repos_are_not_indexed(tmp_path, monkeypatch):
    active = tmp_path / 'active'
    dormant = tmp_path / 'dormant'
    active.mkdir()
    dormant.mkdir()
    write_registries(
        tmp_path,
        monkeypatch,
        portfolio=[
            {'name': 'active', 'path': str(active), 'status': 'active'},
            {'name': 'dormant', 'path': str(dormant), 'status': 'dormant'},
        ],
        exemplars=[],
    )
    assert repos.get_owned_repo_names() == {'active'}


def test_a_repo_with_no_status_is_rejected(tmp_path, monkeypatch):
    """Skipping it silently is what let this reader disagree with fleet about which
    repos exist. fleet counted a status-less entry as active, indy dropped it, and
    both produced a plausible list with no way to notice the difference."""
    active = tmp_path / 'active'
    undeclared = tmp_path / 'undeclared'
    active.mkdir()
    undeclared.mkdir()
    write_registries(
        tmp_path,
        monkeypatch,
        portfolio=[
            {'name': 'active', 'path': str(active), 'status': 'active'},
            {'name': 'undeclared', 'path': str(undeclared)},
        ],
        exemplars=[],
    )
    with pytest.raises(ValueError, match='undeclared'):
        repos.get_owned_repo_names()


def test_missing_exemplar_file_is_not_fatal(tmp_path, monkeypatch):
    repo = tmp_path / 'example-repo'
    repo.mkdir()
    write_registries(tmp_path, monkeypatch, portfolio=[{'name': 'example-repo', 'path': str(repo), 'status': 'active'}], exemplars=[])
    monkeypatch.setattr(repos, 'EXEMPLAR_REPOS_FILE', tmp_path / 'does-not-exist.json')
    assert repos.get_exemplar_repo_names() == set()
    assert repos.get_owned_repo_names() == {'example-repo'}


def test_exemplar_carries_its_exclude_patterns(tmp_path, monkeypatch):
    repo = tmp_path / 'fastapi'
    repo.mkdir()
    write_registries(
        tmp_path,
        monkeypatch,
        portfolio=[],
        exemplars=[{'name': 'fastapi', 'path': str(repo), 'owner': 'fastapi', 'index_exclude': ['tests/**']}],
    )
    target = repos.get_target_by_name('fastapi/fastapi')
    assert target.exclude == ('tests/**',)


@pytest.mark.parametrize(
    ('path', 'patterns', 'expected'),
    [
        # Last match wins, so a negation re-includes one branch of a dropped tree.
        ('docs/en/tutorial/body.md', ('docs/*/**', '!docs/en/**'), False),
        ('docs/de/tutorial/body.md', ('docs/*/**', '!docs/en/**'), True),
        ('docs/zh-hant/index.md', ('docs/*/**', '!docs/en/**'), True),
        ('fastapi/routing.py', ('docs/*/**', '!docs/en/**'), False),
        ('tests/test_router.py', ('tests/**',), True),
        ('sklearn/linear_model/tests/test_base.py', ('sklearn/**/tests/**',), True),
        ('sklearn/linear_model/_base.py', ('sklearn/**/tests/**',), False),
        # examples/ is the exemplar material in bubbletea and lipgloss — never dropped.
        ('examples/simple/main.go', ('testdata/**',), False),
        ('README.md', (), False),
    ],
)
def test_is_excluded(path, patterns, expected):
    assert is_excluded(path, patterns) is expected


def test_index_target_defaults_to_no_excludes():
    assert IndexTarget(name='example-repo', path='/tmp/example-repo', kind=repos.OWNED).exclude == ()
