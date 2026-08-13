from pathlib import Path

import pytest

from indy import config


def test_defaults_name_no_path_outside_indys_own_dirs(monkeypatch):
    """A default that names someone's directory layout is the bug this guards.

    Resolved with an empty config and no environment, which is what "default" means —
    asserting on the module constants instead would just re-read whatever config file
    the machine running the tests happens to have.
    """
    for env_var in ('INDY_DIR', 'INDY_REPOS_REGISTRY', 'INDY_EXEMPLAR_REGISTRY'):
        monkeypatch.delenv(env_var, raising=False)

    data_dir = config.resolve_path('INDY_DIR', 'data_dir', config.DATA_HOME / 'indy', {})
    repos = config.resolve_path('INDY_REPOS_REGISTRY', 'repos_registry', config.CONFIG_DIR / 'repos.json', {})
    exemplars = config.resolve_path('INDY_EXEMPLAR_REGISTRY', 'exemplar_registry', config.CONFIG_DIR / 'exemplar-repos.json', {})

    assert data_dir == config.DATA_HOME / 'indy'
    assert repos.parent == config.CONFIG_DIR
    assert exemplars.parent == config.CONFIG_DIR


def test_config_file_supplies_a_path(tmp_path, monkeypatch):
    monkeypatch.delenv('INDY_REPOS_REGISTRY', raising=False)
    resolved = config.resolve_path(
        'INDY_REPOS_REGISTRY', 'repos_registry', tmp_path / 'default.json', {'repos_registry': '~/shared/repos.json'}
    )
    assert resolved == Path.home() / 'shared' / 'repos.json'


def test_env_var_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv('INDY_REPOS_REGISTRY', str(tmp_path / 'from-env.json'))
    resolved = config.resolve_path(
        'INDY_REPOS_REGISTRY', 'repos_registry', tmp_path / 'default.json', {'repos_registry': '~/from-config.json'}
    )
    assert resolved == tmp_path / 'from-env.json'


def test_default_applies_when_neither_layer_names_one(tmp_path, monkeypatch):
    monkeypatch.delenv('INDY_REPOS_REGISTRY', raising=False)
    assert config.resolve_path('INDY_REPOS_REGISTRY', 'repos_registry', tmp_path / 'default.json', {}) == tmp_path / 'default.json'


def test_an_unprefixed_variable_is_never_consulted(tmp_path, monkeypatch):
    """indy reads no variable it does not own, so the machine layer is config.toml alone.

    A shared `REPOS_JSON` rung used to sit between the config key and the default. It came
    out because `~/.env` does not reach a process that sources no profile, which made the
    rung empty in exactly the unattended runs it was meant to serve.
    """
    monkeypatch.delenv('INDY_REPOS_REGISTRY', raising=False)
    monkeypatch.setenv('REPOS_JSON', str(tmp_path / 'declared.json'))

    assert config.resolve_path('INDY_REPOS_REGISTRY', 'repos_registry', tmp_path / 'default.json', {}) == tmp_path / 'default.json'
    assert config.setting_source('INDY_REPOS_REGISTRY', 'repos_registry', {}) == 'default'


def test_extra_paths_default_to_none(monkeypatch):
    """Which loose directories are worth indexing is a property of the machine."""
    monkeypatch.delenv('INDY_EXTRA_PATHS', raising=False)
    assert config.resolve_extra_paths({}) == []


def test_extra_paths_come_from_the_config_file(monkeypatch):
    monkeypatch.delenv('INDY_EXTRA_PATHS', raising=False)
    entries = [{'name': 'notes', 'path': '~/notes'}]
    assert config.resolve_extra_paths({'extra_paths': entries}) == entries


def test_extra_paths_env_var_takes_json(monkeypatch):
    monkeypatch.setenv('INDY_EXTRA_PATHS', '[{"name": "scratch", "path": "/tmp/scratch"}]')
    assert config.resolve_extra_paths({'extra_paths': [{'name': 'notes', 'path': '~/notes'}]}) == [
        {'name': 'scratch', 'path': '/tmp/scratch'}
    ]


def test_missing_config_file_is_not_fatal(tmp_path):
    assert config.load_user_config(tmp_path / 'absent.toml') == {}


def test_config_file_is_parsed(tmp_path):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('repos_registry = "~/dev/repos.json"\n\n[[extra_paths]]\nname = "notes"\npath = "~/notes"\n')
    parsed = config.load_user_config(config_file)
    assert parsed['repos_registry'] == '~/dev/repos.json'
    assert parsed['extra_paths'] == [{'name': 'notes', 'path': '~/notes'}]


def test_setting_source_names_the_layer(monkeypatch):
    monkeypatch.delenv('INDY_REPOS_REGISTRY', raising=False)
    assert config.setting_source('INDY_REPOS_REGISTRY', 'repos_registry', {}) == 'default'
    assert config.setting_source('INDY_REPOS_REGISTRY', 'repos_registry', {'repos_registry': '~/x.json'}) == str(config.CONFIG_PATH)
    monkeypatch.setenv('INDY_REPOS_REGISTRY', '/tmp/x.json')
    assert config.setting_source('INDY_REPOS_REGISTRY', 'repos_registry', {'repos_registry': '~/x.json'}) == '$INDY_REPOS_REGISTRY'


def test_a_key_indy_does_not_read_is_refused(tmp_path):
    """Measured 2026-08-13: `exemplar_repos_file` sat in the deployed config after the key
    became `exemplar_registry`. indy read the file, dropped the key, resolved its own config
    directory and found nothing — indistinguishable from a machine that declared no exemplar
    registry at all."""
    config_file = tmp_path / 'config.toml'
    config_file.write_text('exemplar_repos_file = "~/dev/exemplar-repos.json"\n')
    with pytest.raises(config.ConfigError) as exc:
        config.load_user_config(config_file)
    assert 'exemplar_repos_file' in str(exc.value)


def test_every_key_indy_reads_is_accepted(tmp_path):
    """The guard fails closed, so a real key left out of KNOWN_KEYS would break a valid config."""
    config_file = tmp_path / 'config.toml'
    body = '\n'.join(f'{key} = "x"' for key in sorted(config.KNOWN_KEYS - {'extra_paths'}))
    config_file.write_text(body + '\n\n[[extra_paths]]\nname = "n"\npath = "~/n"\n')
    assert sorted(config.load_user_config(config_file)) == sorted(config.KNOWN_KEYS)
