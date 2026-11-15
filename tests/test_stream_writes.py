"""stdout carries a command's data and stderr everything else, so every write goes through one of two doors.

The doors are `console` (stdout) and `err_console` (stderr), built once in main.py. A bare
`print`, a write to `sys.stdout` or `sys.stderr`, or a `Console()` built anywhere else picks a
stream on its own. It compiles and passes lint, and one stray line on stdout breaks every
caller that parses `--json`.
"""

import ast
from pathlib import Path

import pytest

import indy

PACKAGE_DIR = Path(indy.__file__).parent
DOORS = {'console', 'err_console'}

# Floors, so a walk that reads nothing, or misses the doors, cannot pass.
MIN_FILES = 9
MIN_DOOR_WRITES = 20


def parented(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]
    return tree


def is_door_assignment(call: ast.Call) -> bool:
    """`console = Console(...)` or `err_console = Console(...)` at module level."""
    assign = getattr(call, 'parent', None)
    return (
        isinstance(assign, ast.Assign)
        and isinstance(getattr(assign, 'parent', None), ast.Module)
        and all(isinstance(t, ast.Name) and t.id in DOORS for t in assign.targets)
    )


def bypasses(source: str, *, may_build_doors: bool) -> list[int]:
    """Line numbers of every write that picks a stream without going through a door."""
    found = []
    for node in ast.walk(parented(ast.parse(source))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'print':
            found.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in {'stdout', 'stderr'}:
            if isinstance(node.value, ast.Name) and node.value.id == 'sys':
                parent = getattr(node, 'parent', None)
                if not (isinstance(parent, ast.Attribute) and parent.attr == 'isatty'):
                    found.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == 'sys':
            if any(alias.name in {'stdout', 'stderr'} for alias in node.names):
                found.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''
            if name == 'Console' and not (may_build_doors and is_door_assignment(node)):
                found.append(node.lineno)
    return sorted(found)


def door_writes(source: str) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in DOORS
        for node in ast.walk(ast.parse(source))
    )


def test_every_write_goes_through_a_console_main_built():
    files = sorted(PACKAGE_DIR.rglob('*.py'))
    found = {}
    writes = 0
    for path in files:
        source = path.read_text()
        lines = bypasses(source, may_build_doors=path.name == 'main.py')
        if lines:
            found[str(path.relative_to(PACKAGE_DIR))] = lines
        writes += door_writes(source)

    assert len(files) >= MIN_FILES
    assert writes >= MIN_DOOR_WRITES
    assert found == {}


@pytest.mark.parametrize(
    'source',
    [
        "print('x')",
        "import sys\nsys.stdout.write('x')",
        "import sys\nsys.stderr.write('x')",
        'import sys\njson.dump({}, sys.stdout)',
        'from sys import stdout',
        'console = Console()',
        'def f():\n    con = Console()',
        'import rich.console\nc = rich.console.Console()',
    ],
)
def test_each_way_of_picking_a_stream_is_a_bypass(source):
    assert bypasses(source, may_build_doors=False) == [source.count('\n') + 1]


def test_the_doors_and_a_tty_check_are_not_bypasses():
    source = 'console = Console()\nerr_console = Console(stderr=True)\nimport sys\nsys.stderr.isatty()'
    assert bypasses(source, may_build_doors=True) == []
