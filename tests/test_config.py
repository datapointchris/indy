from pathlib import Path

from indy import config


def test_defaults_name_no_path_outside_indys_own_dirs(monkeypatch):
    """A default that names someone's directory layout is the bug this guards.

    Resolved with an empty config and no environment, which is what "default" means —
    asserting on the module constants instead would just re-read whatever config file
    the machine running the tests happens to have.
    """
    for env_var in ('INDY_DIR', 'INDY_REPOS_FILE', 'INDY_EXEMPLAR_REPOS_FILE'):
        monkeypatch.delenv(env_var, raising=False)

    data_dir = config.resolve_path('INDY_DIR', 'data_dir', config.DATA_HOME / 'indy', {})
    repos_file = config.resolve_path('INDY_REPOS_FILE', 'repos_file', config.CONFIG_DIR / 'repos.json', {})
    exemplars = config.resolve_path('INDY_EXEMPLAR_REPOS_FILE', 'exemplar_repos_file', config.CONFIG_DIR / 'exemplar-repos.json', {})

    assert data_dir == config.DATA_HOME / 'indy'
    assert repos_file.parent == config.CONFIG_DIR
    assert exemplars.parent == config.CONFIG_DIR


def test_config_file_supplies_a_path(tmp_path, monkeypatch):
    monkeypatch.delenv('INDY_REPOS_FILE', raising=False)
    resolved = config.resolve_path('INDY_REPOS_FILE', 'repos_file', tmp_path / 'default.json', {'repos_file': '~/shared/repos.json'})
    assert resolved == Path.home() / 'shared' / 'repos.json'


def test_env_var_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv('INDY_REPOS_FILE', str(tmp_path / 'from-env.json'))
    resolved = config.resolve_path('INDY_REPOS_FILE', 'repos_file', tmp_path / 'default.json', {'repos_file': '~/from-config.json'})
    assert resolved == tmp_path / 'from-env.json'


def test_default_applies_when_neither_layer_names_one(tmp_path, monkeypatch):
    monkeypatch.delenv('INDY_REPOS_FILE', raising=False)
    assert config.resolve_path('INDY_REPOS_FILE', 'repos_file', tmp_path / 'default.json', {}) == tmp_path / 'default.json'


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
    config_file.write_text('repos_file = "~/dev/repos.json"\n\n[[extra_paths]]\nname = "notes"\npath = "~/notes"\n')
    parsed = config.load_user_config(config_file)
    assert parsed['repos_file'] == '~/dev/repos.json'
    assert parsed['extra_paths'] == [{'name': 'notes', 'path': '~/notes'}]


def test_setting_source_names_the_layer(monkeypatch):
    monkeypatch.delenv('INDY_REPOS_FILE', raising=False)
    assert config.setting_source('INDY_REPOS_FILE', 'repos_file', {}) == 'default'
    assert config.setting_source('INDY_REPOS_FILE', 'repos_file', {'repos_file': '~/x.json'}) == str(config.CONFIG_PATH)
    monkeypatch.setenv('INDY_REPOS_FILE', '/tmp/x.json')
    assert config.setting_source('INDY_REPOS_FILE', 'repos_file', {'repos_file': '~/x.json'}) == '$INDY_REPOS_FILE'
