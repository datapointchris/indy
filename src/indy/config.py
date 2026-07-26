import json
import os
from pathlib import Path

INDY_DIR = Path(os.environ.get('INDY_DIR', Path.home() / 'dev' / 'indy'))
DB_PATH = INDY_DIR / 'indy.db'
LOG_PATH = INDY_DIR / 'indy.log'

REPOS_FILE = Path(os.environ.get('INDY_REPOS_FILE', Path.home() / 'dev' / 'repos.json'))

# Third-party clones kept as code exemplars. Separate from repos.json because the two
# answer different questions and need different fields: an exemplar carries what it is
# exemplary for and which subtrees to keep out of the index, neither of which belongs
# on a repo we actually work in.
EXEMPLAR_REPOS_FILE = Path(os.environ.get('INDY_EXEMPLAR_REPOS_FILE', Path.home() / 'dev' / 'exemplar-repos.json'))

# Non-repo paths included in default 'indy index' runs (alongside repos.json).
# The Syncthing-synced personal-knowledge dirs, which hold the reasoning behind decisions
# that no repo records. ~/shart is deliberately absent: it holds recovery codes and key
# material in .txt files, which DOC_EXTENSIONS would otherwise pull into the index.
# Override with INDY_EXTRA_PATHS as JSON: '[{"name":"notes","path":"~/notes"}]'
DEFAULT_EXTRA_PATHS = (
    '[{"name": "obsession", "path": "~/obsession"}, {"name": "notes", "path": "~/notes"}, {"name": "dev", "path": "~/dev"}]'
)
EXTRA_PATHS_RAW: list[dict] = json.loads(os.environ.get('INDY_EXTRA_PATHS', DEFAULT_EXTRA_PATHS))

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
