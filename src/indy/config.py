import json
import os
from pathlib import Path

# Derived per-machine state, so it must stay out of ~/dev: that tree is
# Syncthing-synced, and file-syncing a live WAL-mode SQLite DB corrupts it.
XDG_DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
INDY_DIR = Path(os.environ.get('INDY_DIR', XDG_DATA_HOME / 'indy'))
DB_PATH = INDY_DIR / 'indy.db'
LOG_PATH = INDY_DIR / 'indy.log'

REPOS_FILE = Path(os.environ.get('INDY_REPOS_FILE', Path.home() / 'dev' / 'repos.json'))

# Non-repo paths included in default 'indy index' runs (alongside repos.json).
# Override with INDY_EXTRA_PATHS as JSON: '[{"name":"notes-dev","path":"~/notes/dev"}]'
EXTRA_PATHS_RAW: list[dict] = json.loads(os.environ.get('INDY_EXTRA_PATHS', '[{"name": "notes-dev", "path": "~/notes/dev"}]'))

# Ollama embedding config — httpx calls, no Python package needed
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'nomic-embed-text')
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
