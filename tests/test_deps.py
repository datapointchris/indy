"""`deps Owner.member` follows a call chain through a dispatch table, `self` and an annotated parameter.

The fixture repo is one real chain in miniature. `write_wslconfig` is reached only as a dict
value, `StepProvider.repair` only through `self`, and `SystemConfigProvider.install` only
through a parameter annotated with its base class.
"""

import pytest

from indy import config
from indy import service
from indy import storage
from indy.chunker import EXTRACTOR_VERSION

STEPS_PY = """\
def write_wslconfig():
    return 'written'


def unprivileged(applier):
    return applier


STEPS = {'wslconfig': unprivileged(write_wslconfig)}


def apply(name):
    return STEPS[name]()


def apply_all(names):
    return [apply(name) for name in names]
"""

REGISTRY_PY = """\
from pkg import steps


class Provider:
    def install(self, item):
        raise NotImplementedError

    def evidence(self, item):
        return item


class SystemConfigProvider(Provider):
    @property
    def label(self):
        return 'system'

    def repair(self, item):
        return item

    def install(self, item):
        self.evidence(item)
        return self.repair(item)


class StepProvider(SystemConfigProvider):
    def repair(self, item):
        return steps.apply(item)

    def retry(self, item):
        return {self.label: self.repair}


def install_one(provider: 'Provider | None', item):
    return provider.install(item)


def check(provider: StepProvider, item):
    return provider.evidence(item)


def install_each(providers, item):
    for provider in providers:
        provider.install(item)
        provider.uninstall(item)
"""

TEST_STEPS_PY = """\
from pkg import steps


def test_apply():
    assert steps.apply('wslconfig')
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """The fixture repo, indexed once with a stub embedding."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')
    monkeypatch.setattr(service, 'embed_text', lambda text: [0.1] * config.EMBEDDING_DIM)
    root = tmp_path / 'repo'
    for relative, body in {'src/pkg/steps.py': STEPS_PY, 'src/pkg/registry.py': REGISTRY_PY, 'tests/test_steps.py': TEST_STEPS_PY}.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(body)
    service.index_path(root, 'r')
    return root


def callers(symbol: str) -> list[tuple[str, str]]:
    return [(row['source_symbol'], row['via']) for row in service.get_dependencies(symbol, direction='callers')['callers']]


def callees(symbol: str) -> list[tuple[str, str]]:
    return [(row['source_symbol'], row['target_symbol']) for row in service.get_dependencies(symbol, direction='callees')['callees']]


@pytest.mark.parametrize(
    ('symbol', 'expected'),
    [
        pytest.param('write_wslconfig', [("STEPS['wslconfig']", 'reference')], id='a function reached only as a dict value'),
        pytest.param(
            'StepProvider.repair',
            [('StepProvider.retry', 'self'), ('SystemConfigProvider.install', 'self')],
            id='an override called through self in its base class, and passed as a bound method',
        ),
        pytest.param(
            'SystemConfigProvider.repair',
            [('SystemConfigProvider.install', 'self')],
            id='a subclass that overrides the method keeps its self calls to itself',
        ),
        pytest.param(
            'Provider.evidence',
            [('SystemConfigProvider.install', 'self'), ('check', 'typed')],
            id='a base-class method reached through a subclass self call and a subclass-annotated parameter',
        ),
        pytest.param('SystemConfigProvider.label', [('StepProvider.retry', 'self')], id='a property read through self in a subclass'),
        pytest.param(
            'SystemConfigProvider.install',
            [('install_one', 'typed'), ('install_each', 'candidate')],
            id='an untyped caller is listed as a candidate after the typed one',
        ),
        pytest.param(
            'steps.apply',
            [('StepProvider.repair', 'call'), ('apply_all', 'module'), ('test_apply', 'call')],
            id='a module function, tests last',
        ),
        pytest.param('Provider.uninstall', [('install_each', 'candidate')], id='a method nothing types'),
        pytest.param("STEPS['wslconfig']", [('apply', 'reference')], id='a dispatch entry pasted back from the output'),
    ],
)
def test_each_caller_is_found_by_the_rule_that_reaches_it(repo, symbol, expected):
    assert callers(symbol) == expected


@pytest.mark.parametrize(
    ('symbol', 'expected'),
    [
        pytest.param('steps.apply_all', [('apply_all', 'apply')], id='a module function named module.function, as --help offers'),
        pytest.param('StepProvider.repair', [('StepProvider.repair', 'apply')], id='a method'),
        pytest.param('STEPS', [("STEPS['wslconfig']", 'unprivileged')], id='a dispatch table, across its keys'),
        pytest.param("STEPS['wslconfig']", [("STEPS['wslconfig']", 'unprivileged')], id='a dispatch entry pasted back from the output'),
    ],
)
def test_callees_resolve_for_every_name_deps_prints_or_offers(repo, symbol, expected):
    assert callees(symbol) == expected


def test_a_new_extractor_reaches_files_whose_content_did_not_change(repo, monkeypatch):
    """An unchanged file is skipped before embedding, so it would otherwise keep the old extractor's rows for good.

    Dropping the column puts the index back in the state a release without it left behind.
    """
    conn = storage.open_working_db()
    conn.execute('DELETE FROM symbol_reference')
    conn.execute('ALTER TABLE indexed_file DROP COLUMN extractor_version')
    storage.commit_working_db(conn)

    embedded: list[str] = []
    monkeypatch.setattr(service, 'embed_text', lambda text: embedded.append(text) or [0.1] * config.EMBEDDING_DIM)
    service.index_path(repo, 'r')

    assert embedded == []
    assert callers('write_wslconfig') == [("STEPS['wslconfig']", 'reference')]
    read = storage.get_read_db()
    assert {row[0] for row in read.execute('SELECT extractor_version FROM indexed_file')} == {EXTRACTOR_VERSION}
    read.close()
