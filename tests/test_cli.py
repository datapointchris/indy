"""The machine contract: what a caller shelling out to indy can rely on."""

import pytest
from typer.testing import CliRunner

from indy import service
from indy.main import indy_app

runner = CliRunner()


def test_version_is_one_line_naming_the_tool_and_exits_clean():
    """The question every CLI here answers the same way, so a script can ask it."""
    result = runner.invoke(indy_app, ['--version'])
    assert result.exit_code == 0
    assert result.stdout.startswith('indy ')
    assert len(result.stdout.strip().splitlines()) == 1


def test_bare_invocation_shows_help_rather_than_doing_anything():
    """Help on stdout and exit 2 — incomplete command, not a failed one."""
    result = runner.invoke(indy_app, [])
    assert result.exit_code == 2
    assert 'Usage:' in result.output


def test_forgetting_an_unknown_label_is_a_usage_error(monkeypatch):
    """Exit 2, so a typo is distinguishable from a label that was there and failed to go."""
    monkeypatch.setattr(service, 'indexed_repo_names', lambda: ['notes'])

    result = runner.invoke(indy_app, ['forget', 'nope'])

    assert result.exit_code == 2
    assert 'indy repos' in result.output


def test_an_unknown_label_never_reaches_the_index(monkeypatch):
    """The check is ahead of the session on purpose: a snapshot of the whole database is the
    cost of opening one, and a typo should not pay it."""
    monkeypatch.setattr(service, 'indexed_repo_names', lambda: ['notes'])
    monkeypatch.setattr(service, 'forget_repo', lambda *a, **k: pytest.fail('opened a session for a typo'))

    assert runner.invoke(indy_app, ['forget', 'nope']).exit_code == 2
