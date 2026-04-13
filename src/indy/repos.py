import json
from pathlib import Path

from indy.config import EXTRA_PATHS_RAW
from indy.config import REPOS_FILE


def load_repos_data() -> dict:
    """Load and return the raw repos.json data."""
    return json.loads(REPOS_FILE.read_text())


def load_active_repos() -> list[dict]:
    """Read repos.json and return active repos with resolved paths that exist on disk."""
    data = load_repos_data()

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


def get_owned_repo_names() -> set[str]:
    """Return names of repos owned by the top-level owner (or with no explicit owner)."""
    data = load_repos_data()
    default_owner = data.get('owner', '')
    names = set()
    for repo in data.get('repos', []):
        if repo.get('status') != 'active':
            continue
        repo_owner = repo.get('owner', default_owner)
        if repo_owner == default_owner:
            names.add(repo['name'])
    return names


def get_reference_repo_names() -> set[str]:
    """Return names of repos whose owner differs from the top-level owner."""
    data = load_repos_data()
    default_owner = data.get('owner', '')
    names = set()
    for repo in data.get('repos', []):
        if repo.get('status') != 'active':
            continue
        repo_owner = repo.get('owner', default_owner)
        if repo_owner != default_owner:
            names.add(repo['name'])
    return names
