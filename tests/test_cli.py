"""The machine contract: what a caller shelling out to indy can rely on."""

from typer.testing import CliRunner

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
