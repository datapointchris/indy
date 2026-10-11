"""`deps` takes a repo's callers from scip-python where the last index ran it, and the syntactic rules everywhere else.

The fixture repo holds what only a type checker can follow. `install_all` calls
`Provider.install_all` through a local assigned from a function's return, `stock` calls a
method of the same name on another class, and `untyped` calls one on a parameter nothing
types. `CaskProvider.install` is reached only through the base method it overrides.
"""

import shutil
import subprocess
import sys
from contextlib import closing

import pytest

from indy import config
from indy import scip
from indy import service
from indy import storage
from indy.repos import OWNED
from indy.repos import IndexTarget

REGISTRY_PY = """\
class Provider:
    def install_all(self, items):
        return [self.install(item) for item in items]

    def install(self, item):
        return item


class CaskProvider(Provider):
    def install(self, cask):
        return cask


class Shelf:
    def install_all(self, shelved):
        return shelved


def named(name: str) -> Provider:
    return CaskProvider()


def install_all(name, items):
    provider = named(name)
    return provider.install_all(items)


def stock(items):
    shelf = Shelf()
    return shelf.install_all(items)


def untyped(things, items):
    return things.install_all(items)
"""

MODULE = 'scip-python python r 0 `pkg.registry`/'


def at(line_text: str, token: str, symbol: str, roles: int = 0, after: str = '') -> scip.Occurrence:
    """The occurrence of token on the fixture line reading line_text, past `after`, as scip-python writes one."""
    lines = REGISTRY_PY.splitlines()
    line = next(number for number, text in enumerate(lines) if text.strip() == line_text)
    column = lines[line].index(token, lines[line].index(after) + len(after) if after else 0)
    return scip.Occurrence(line, column, line, column + len(token), MODULE + symbol, roles)


def registry_document() -> scip.Document:
    """What scip-python 0.6.6 writes for REGISTRY_PY, less the locals and parameters indy ignores."""
    defines = scip.ROLE_DEFINITION
    return scip.Document(
        'src/pkg/registry.py',
        [
            at('class Provider:', 'Provider', 'Provider#', defines),
            at('def install_all(self, items):', 'install_all', 'Provider#install_all().', defines),
            at('return [self.install(item) for item in items]', 'install', 'Provider#install().'),
            at('def install(self, item):', 'install', 'Provider#install().', defines),
            at('class CaskProvider(Provider):', 'CaskProvider', 'CaskProvider#', defines),
            at('class CaskProvider(Provider):', 'Provider', 'Provider#', after='('),
            at('def install(self, cask):', 'install', 'CaskProvider#install().', defines),
            at('class Shelf:', 'Shelf', 'Shelf#', defines),
            at('def install_all(self, shelved):', 'install_all', 'Shelf#install_all().', defines),
            at('def named(name: str) -> Provider:', 'named', 'named().', defines),
            at('def named(name: str) -> Provider:', 'Provider', 'Provider#'),
            at('return CaskProvider()', 'CaskProvider', 'CaskProvider#'),
            at('def install_all(name, items):', 'install_all', 'install_all().', defines),
            at('provider = named(name)', 'named', 'named().'),
            at('return provider.install_all(items)', 'install_all', 'Provider#install_all().'),
            at('def stock(items):', 'stock', 'stock().', defines),
            at('shelf = Shelf()', 'Shelf', 'Shelf#'),
            at('return shelf.install_all(items)', 'install_all', 'Shelf#install_all().'),
            at('def untyped(things, items):', 'untyped', 'untyped().', defines),
        ],
        [(MODULE + 'CaskProvider#install().', 'implementation', MODULE + 'Provider#install().')],
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """The fixture repo, with a venv in place, and a stub embedding standing in for ollama."""
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'indy.db')
    monkeypatch.setattr(storage, 'WORKING_DB_PATH', tmp_path / 'indy.db.building')
    monkeypatch.setattr(service, 'embed_text', lambda text: [0.1] * config.EMBEDDING_DIM)
    root = tmp_path / 'r'
    (root / 'src' / 'pkg').mkdir(parents=True)
    (root / 'src' / 'pkg' / 'registry.py').write_text(REGISTRY_PY)
    (root / '.venv' / 'bin').mkdir(parents=True)
    (root / '.venv' / 'bin' / 'python').touch()
    return root


@pytest.fixture
def scip_runs(monkeypatch):
    """scip-python stubbed to write registry_document(). Each run appends the root it ran over."""
    runs = []
    monkeypatch.setattr(service, 'describe_environment', lambda python: '[]')

    def run(root, project_name, environment):
        runs.append(root)
        return [registry_document()]

    monkeypatch.setattr(service, 'run_scip_python', run)
    return runs


def index(*roots) -> list[dict]:
    """Index each root as an owned repo named after its directory, all in one session."""
    with service.index_session() as session:
        return [session.index_target(IndexTarget(root.name, root, OWNED)) for root in roots]


def callers(symbol: str) -> list[tuple[str, str]]:
    return [(row['source_symbol'], row['via']) for row in service.get_dependencies(symbol, direction='callers')['callers']]


SCIP_CALLERS = [
    pytest.param(
        'Provider.install_all',
        [('install_all', 'inferred'), ('untyped', 'candidate')],
        id='a local typed by inference resolves, a same-named method elsewhere drops, an untyped call stays a candidate',
    ),
    pytest.param('CaskProvider.install', [('Provider.install_all', 'override')], id='a call to the base method an override replaces'),
    pytest.param('Provider.install', [('Provider.install_all', 'self')], id='a caller the syntactic rules also reach keeps their label'),
    pytest.param('Provider', [], id='an annotation and a class base are not uses'),
    pytest.param('named', [('install_all', 'call')], id='a bare name'),
]


@pytest.mark.parametrize(('symbol', 'expected'), SCIP_CALLERS)
def test_callers_come_from_scip_python_in_a_repo_it_indexed(repo, scip_runs, symbol, expected):
    index(repo)
    assert callers(symbol) == expected


def test_a_callee_prints_under_the_class_scip_python_resolved_it_to(repo, scip_runs):
    index(repo)
    callees = service.get_dependencies('registry.install_all', direction='callees')['callees']
    assert sorted((row['target_module'], row['target_symbol']) for row in callees) == [('Provider', 'install_all'), ('registry', 'named')]


def test_a_file_the_overlay_holds_answers_from_its_syntactic_rows(repo, scip_runs):
    """A saved file's SCIP links describe content that is gone, so the overlay's rows for it stand instead."""
    index(repo)
    with closing(storage.open_overlay_db()) as overlay:
        storage.compact_overlay(overlay)
        path = repo / 'src' / 'pkg' / 'registry.py'
        path.write_text(REGISTRY_PY + '\n\ndef added():\n    return 1\n')
        assert service.update_overlay(overlay, path, service.LiveRoot(repo, 'r')) is not None

    found = service.get_dependencies('Provider.install_all', direction='callers')
    assert [(row['source_symbol'], row['via']) for row in found['callers']] == [
        ('install_all', 'candidate'),
        ('stock', 'candidate'),
        ('untyped', 'candidate'),
    ]
    assert found['resolved_by_scip'] == []


def test_a_repo_scip_python_cannot_index_is_recorded_and_the_run_goes_on(repo, scip_runs, monkeypatch, tmp_path):
    """No venv, no Python, and a failing run each leave a reason, and every repo still indexes."""
    no_venv = tmp_path / 'no-venv'
    shutil.copytree(repo, no_venv, ignore=shutil.ignore_patterns('.venv'))
    no_python = tmp_path / 'no-python'
    no_python.mkdir()
    (no_python / 'README.md').write_text('# docs\n')
    broken = tmp_path / 'broken'
    shutil.copytree(repo, broken)

    def run(root, project_name, environment):
        if root == broken:
            raise scip.ScipError('scip-python exited 1: Error: boom')
        return [registry_document()]

    monkeypatch.setattr(service, 'run_scip_python', run)
    results = index(no_venv, no_python, broken)

    assert [result['error'] for result in results] == [None, None, None]
    states = {state['repo']: (state['status'], state['reason']) for state in service.get_status()['scip']}
    assert states == {
        'no-venv': ('skipped', service.NO_VENV_REASON),
        'no-python': ('skipped', 'no Python files'),
        'broken': ('failed', 'scip-python exited 1: Error: boom'),
    }
    assert callers('Provider.install_all') == [('install_all', 'candidate'), ('stock', 'candidate'), ('untyped', 'candidate')] * 2


def test_scip_python_runs_again_only_once_a_python_file_changes(repo, scip_runs):
    first, again = index(repo), index(repo)
    assert (first[0]['scip']['status'], again[0]['scip'].get('unchanged')) == ('ok', True)
    assert len(scip_runs) == 1

    (repo / 'src' / 'pkg' / 'registry.py').write_text(REGISTRY_PY + '\n')
    index(repo)
    assert len(scip_runs) == 2
    assert callers('CaskProvider.install') == [('Provider.install_all', 'override')]


# ── decoding ──────────────────────────────────────────────────────────────────


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def message(*fields: tuple[int, int | bytes | str]) -> bytes:
    """A protobuf message: an int is a varint field, anything else length-delimited."""
    out = b''
    for number, value in fields:
        if isinstance(value, int):
            out += varint(number << 3) + varint(value)
        else:
            data = value.encode() if isinstance(value, str) else value
            out += varint(number << 3 | 2) + varint(len(data)) + data
    return out


def packed(*numbers: int) -> bytes:
    return b''.join(varint(number) for number in numbers)


def test_the_decoder_reads_the_fields_scip_proto_defines_and_skips_the_rest():
    relationship = message((1, 'base().'), (3, 1))
    information = message((1, 'derived().'), (3, 'documentation'), (4, relationship))
    document = message(
        (1, 'src/a.py'),
        (2, message((1, packed(3, 4, 9)), (2, 'derived().'), (3, scip.ROLE_DEFINITION))),
        (2, message((1, packed(5, 0, 6, 2)), (2, 'base().'), (3, 8), (7, packed(1, 0, 9, 0)))),
        (3, information),
        (4, 'python'),
    )
    index_bytes = message((1, message((1, 0))), (2, document)) + varint(5 << 3 | 5) + b'\0\0\0\0'

    assert scip.decode_index(index_bytes) == [
        scip.Document(
            'src/a.py',
            [scip.Occurrence(3, 4, 3, 9, 'derived().', 1), scip.Occurrence(5, 0, 6, 2, 'base().', 8)],
            [('derived().', 'implementation', 'base().')],
        )
    ]


def test_a_truncated_index_is_refused_rather_than_read_short():
    document = message((1, 'src/a.py'))
    with pytest.raises(scip.ScipError):
        scip.decode_index(message((2, document))[:-3])


PACKAGE = 'scip-python python r 0 '


@pytest.mark.parametrize(
    ('symbol', 'expected'),
    [
        pytest.param(PACKAGE + '`pkg.registry`/Provider#install_all().', ('pkg.registry', 'Provider.install_all', 'method'), id='a method'),
        pytest.param(PACKAGE + '`pkg.steps`/apply().', ('pkg.steps', 'apply', 'function'), id='a module function'),
        pytest.param(PACKAGE + '`pkg.steps`/STEPS.STEPS.', ('pkg.steps', 'STEPS', 'variable'), id='a module variable, written twice over'),
        pytest.param(PACKAGE + '`pkg.steps`/apply().(name)', None, id='a parameter'),
        pytest.param(PACKAGE + '`pkg.steps`/kept().[T]', None, id='a type parameter'),
        pytest.param(PACKAGE + '`pkg.types`/.', None, id='the empty name a PEP 695 type alias gets'),
        pytest.param('local 12', None, id='a local'),
    ],
)
def test_a_symbol_is_named_as_deps_prints_it(symbol, expected):
    named = scip.name_symbol(symbol)
    assert (named and (named.module, named.name, named.kind)) == expected


# ── the real tool ─────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which('npx') is None, reason='scip-python runs through npx')
def test_scip_python_itself_resolves_an_inferred_call_and_an_override(repo):
    """The stub above is checked against what the pinned scip-python actually writes for the fixture."""
    shutil.rmtree(repo / '.venv')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(repo / '.venv')], check=True)
    (state,) = (result['scip'] for result in index(repo))

    assert (state['status'], state['reason']) == ('ok', None)
    assert callers('Provider.install_all') == [('install_all', 'inferred'), ('untyped', 'candidate')]
    assert callers('CaskProvider.install') == [('Provider.install_all', 'override')]
