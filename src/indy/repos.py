import json
from pathlib import Path

from indy.config import EXTRA_PATHS_RAW
from indy.config import REPOS_FILE


def load_active_repos() -> list[dict]:
    """Read repos.json and return active repos with resolved paths that exist on disk."""
    data = json.loads(REPOS_FILE.read_text())

    repos = []
    for repo in data.get('repos', []):
        if repo.get('status') != 'active':
            continue
        path = Path(repo.get('path', '')).expanduser()
        if path.exists():
            repos.append({'name': repo['name'], 'path': path})
    return repos


def load_extra_paths() -> list[dict]:
    """Return configured non-repo paths (EXTRA_PATHS_RAW) that exist on disk."""
    paths = []
    for entry in EXTRA_PATHS_RAW:
        path = Path(entry['path']).expanduser()
        if path.exists():
            paths.append({'name': entry['name'], 'path': path})
    return paths


def get_repo_by_name(name: str) -> dict | None:
    """Look up a single active repo by name. Returns None if not found or not active."""
    for repo in load_active_repos():
        if repo['name'] == name:
            return repo
    return None
