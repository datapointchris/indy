"""Constants, XDG base directories, and the user's config file.

Every path indy reads or writes resolves the same way: an environment variable, then a
key in config.toml, then a tool-owned default. No default names a directory outside
indy's own XDG dirs — where a repo registry lives, or which loose directories are worth
indexing, is a property of the machine and belongs in config.toml.

Three rungs, and indy reads no variable that is not prefixed `INDY_`. A shared variable
naming the registry for every tool at once used to sit between the config key and the
default. It came out because `~/.env` does not reach a process that sources no profile,
so the rung was empty in exactly the unattended runs it was supposed to serve, and the
machine layer is what config.toml already is.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any


def _xdg_base(env_var: str, *fallback: str) -> Path:
    override = os.environ.get(env_var)
    return Path(override).expanduser() if override else Path.home().joinpath(*fallback)


CONFIG_HOME = _xdg_base('XDG_CONFIG_HOME', '.config')
DATA_HOME = _xdg_base('XDG_DATA_HOME', '.local', 'share')

CONFIG_DIR = CONFIG_HOME / 'indy'
CONFIG_PATH = CONFIG_DIR / 'config.toml'


def load_user_config(path: Path | None = None) -> dict[str, Any]:
    """Parse config.toml, tolerating its absence — indy runs on defaults alone."""
    path = CONFIG_PATH if path is None else path
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())


USER_CONFIG = load_user_config()


def resolve_path(
    env_var: str,
    key: str,
    default: Path,
    config: dict[str, Any] | None = None,
) -> Path:
    """Resolve a path: $env_var, then the config key, then the default.

    Each rung answers a different question — the variable is this shell, the config is
    this machine, the default is every machine that says nothing.
    """
    config = USER_CONFIG if config is None else config
    value = os.environ.get(env_var) or config.get(key)
    return Path(value).expanduser() if value else default


def resolve_str(env_var: str, key: str, default: str, config: dict[str, Any] | None = None) -> str:
    config = USER_CONFIG if config is None else config
    return os.environ.get(env_var) or config.get(key) or default


def setting_source(env_var: str, key: str, config: dict[str, Any] | None = None) -> str:
    """Which layer supplied a setting.

    Travels with the value because the value alone does not explain itself: an index that
    searches nothing is usually a config file that was never read, not a wrong path.

    Kept in step with resolve_path by hand. A source that names a layer the value did not
    come from is worse than none, because it answers the question the reader arrived with.
    """
    config = USER_CONFIG if config is None else config
    if os.environ.get(env_var):
        return f'${env_var}'
    if config.get(key):
        return str(CONFIG_PATH)
    return 'default'


def resolve_extra_paths(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Non-repo roots to index, as [[extra_paths]] tables of name and path.

    The env var carries the same shape as JSON, for a one-off run against a directory
    that does not belong in the config file.
    """
    config = USER_CONFIG if config is None else config
    raw = os.environ.get('INDY_EXTRA_PATHS')
    if raw:
        return json.loads(raw)
    return list(config.get('extra_paths', []))


# The index is derived state — rebuildable from its sources, and per-machine unless
# something deliberately shares it. `data_dir` is how a setup that would rather index
# once and search everywhere puts it in a synced tree instead.
INDY_DIR = resolve_path('INDY_DIR', 'data_dir', DATA_HOME / 'indy')
DB_PATH = INDY_DIR / 'indy.db'
LOG_PATH = INDY_DIR / 'indy.log'

# What an index run builds into before renaming the result over DB_PATH. Beside the index
# rather than in a temp directory because os.replace is only atomic within one filesystem,
# and that atomicity is the point — see storage.open_working_db().
WORKING_DB_PATH = INDY_DIR / 'indy.db.building'

# Registry locations default to indy-owned paths. Sharing one registry with other tools
# is an arrangement between those tools, so it belongs in config.toml on the machines
# that have such a file — never in the default, which every machine inherits.
REPOS_REGISTRY = resolve_path('INDY_REPOS_REGISTRY', 'repos_registry', CONFIG_DIR / 'repos.json')
EXEMPLAR_REGISTRY = resolve_path('INDY_EXEMPLAR_REGISTRY', 'exemplar_registry', CONFIG_DIR / 'exemplar-repos.json')

EXTRA_PATHS_RAW: list[dict[str, str]] = resolve_extra_paths()

OLLAMA_HOST = resolve_str('OLLAMA_HOST', 'ollama_host', 'http://localhost:11434')
OLLAMA_MODEL = resolve_str('OLLAMA_MODEL', 'ollama_model', 'nomic-embed-text')
EMBEDDING_DIM = 768  # nomic-embed-text output dimension

# Files to skip regardless of extension
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.mypy_cache', '.ruff_cache', '.pytest_cache'}
SKIP_FILES = {'uv.lock', 'package-lock.json', 'yarn.lock', 'Cargo.lock', 'go.sum'}

# Extensions to index
CODE_EXTENSIONS = {'.py', '.go', '.js', '.ts', '.tsx', '.sh', '.rs', '.rb'}
DOC_EXTENSIONS = {'.md', '.rst', '.txt'}
CONFIG_EXTENSIONS = {'.yaml', '.yml', '.toml', '.json'}

MAX_FILE_SIZE_BYTES = 500_000

HOME_PREFIX = str(Path.home())


def compact_path(absolute_path: str) -> str:
    """Replace the home directory prefix with ~ for cross-machine portability."""
    if absolute_path.startswith(HOME_PREFIX):
        return '~' + absolute_path[len(HOME_PREFIX) :]
    return absolute_path


def expand_path(db_path: str) -> str:
    """Expand a ~-prefixed path back to an absolute path for the current machine."""
    if db_path.startswith('~/'):
        return HOME_PREFIX + db_path[1:]
    return db_path


# Chunking
CODE_CHUNK_SIZE = 600
CODE_CHUNK_OVERLAP = 80
PROSE_CHUNK_SIZE = 400
PROSE_CHUNK_OVERLAP = 50

# nomic-embed-text runs at a 2048-token context under ollama, which 500s rather than
# truncating. Measured boundary is ~10,000 chars of prose; this leaves headroom for
# dense content like minified JSON, which tokenizes closer to 2 chars per token.
WHOLE_FILE_MAX_CHARS = 4_000
