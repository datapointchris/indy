"""Every command indy tells a reader to run exists, and every node of the tree carries examples.

Both are surface invariants, so they are derived from the command tree rather than listed: a
command added, renamed or removed is covered without editing this file.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from indy import main
from indy.main import indy_app

REPO = Path(__file__).resolve().parent.parent
TREE = typer.main.get_command(indy_app)

# A command printed for a person to run: in rich bold, a code span or a quoted string in the
# source, or at the start of a line in one of the README's shell fences. A word running into a
# `/` or a `.` is a path argument rather than a subcommand.
WORDS = r'((?: [a-z][\w-]*(?![\w/.-]))+)'
PRINTED_IN_SOURCE = re.compile(rf"(?:\[bold\]|`|')indy{WORDS}")
SHELL_FENCE = re.compile(r'^```(?:bash|sh)\n(.*?)^```', re.MULTILINE | re.DOTALL)
PRINTED_IN_FENCE = re.compile(rf'^indy{WORDS}', re.MULTILINE)


def printed_commands() -> Iterator[tuple[str, list[str]]]:
    for source in sorted((REPO / 'src' / 'indy').glob('*.py')):
        for match in PRINTED_IN_SOURCE.finditer(source.read_text()):
            yield source.name, match.group(1).split()
    readme = (REPO / 'README.md').read_text()
    for fence in SHELL_FENCE.finditer(readme):
        for match in PRINTED_IN_FENCE.finditer(fence.group(1)):
            yield 'README.md', match.group(1).split()
    for match in PRINTED_IN_SOURCE.finditer(readme):
        yield 'README.md', match.group(1).split()


def every_node(command: click.Command = TREE, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    yield path, command
    for name, child in getattr(command, 'commands', {}).items():
        yield from every_node(child, (*path, name))


def test_every_command_it_prints_is_in_the_command_tree():
    """A hint naming a command that was renamed sends its reader to a usage error."""
    missing = []
    walked = 0
    for source, words in printed_commands():
        walked += 1
        command: click.Command = TREE
        for word in words:
            subcommands = getattr(command, 'commands', None)
            if subcommands is None:
                break
            if word not in subcommands:
                missing.append(f'{source}: indy {" ".join(words)}')
                break
            command = subcommands[word]

    assert walked > 20
    assert missing == []


def test_every_node_carries_examples():
    """A reader who reached a command by tab-completion gets a line to copy, at every level."""
    without = [' '.join(('indy', *path)) for path, command in every_node() if not (command.epilog or '').startswith('Examples:')]

    assert without == []


@pytest.mark.parametrize('path', [path for path, _ in every_node()], ids=lambda path: ' '.join(('indy', *path)))
def test_every_node_answers_help(monkeypatch, path):
    monkeypatch.setattr(main, 'notify', lambda config: None)

    result = CliRunner().invoke(indy_app, [*path, '--help'])

    assert result.exit_code == 0, result.output
