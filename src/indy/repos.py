import json
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from indy.config import EXEMPLAR_REGISTRY
from indy.config import EXTRA_PATHS_RAW
from indy.config import REPOS_REGISTRY

OWNED = 'owned'
EXEMPLAR = 'exemplar'
EXTRA = 'extra'


@dataclass(frozen=True)
class IndexTarget:
    """One indexable root: a portfolio repo, an exemplar clone, or a loose path."""

    name: str
    path: Path
    kind: str
    exclude: tuple[str, ...] = ()


def load_json_file(path: Path) -> dict:
    """Read a registry file, tolerating its absence so one missing file is not fatal."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_portfolio_repos() -> list[IndexTarget]:
    """Active repos we own, from repos.json. Indexed under their bare registry name.

    `status` is required, so a missing one is an error rather than a repo quietly
    left out of the index. Skipping it silently is what let this reader disagree
    with fleet about which repos exist, with neither able to say so.
    """
    repos = load_json_file(REPOS_REGISTRY).get('repos', [])
    missing = [repo.get('name', '(unnamed)') for repo in repos if 'status' not in repo]
    if missing:
        raise ValueError(f'{REPOS_REGISTRY}: status is required and is missing on: {", ".join(missing)}')

    targets = []
    for repo in repos:
        if repo['status'] != 'active':
            continue
        path = Path(repo.get('path', '')).expanduser()
        if path.exists():
            targets.append(IndexTarget(name=repo['name'], path=path, kind=OWNED))
    return targets


def load_exemplar_repos() -> list[IndexTarget]:
    """Third-party clones kept as code exemplars, from exemplar-repos.json.

    Indexed under a qualified `owner/name`. Bare names collide with the portfolio —
    a clone commonly shares its name with a repo you own — which put one name in both
    the owned and exemplar sets and merged their chunks under one label.
    """
    targets = []
    for repo in load_json_file(EXEMPLAR_REGISTRY).get('repos', []):
        path = Path(repo.get('path', '')).expanduser()
        if not path.exists():
            continue
        targets.append(
            IndexTarget(
                name=f'{repo["owner"]}/{repo["name"]}',
                path=path,
                kind=EXEMPLAR,
                exclude=tuple(repo.get('index_exclude', [])),
            )
        )
    return targets


def load_extra_paths() -> list[IndexTarget]:
    """Configured non-repo paths (EXTRA_PATHS_RAW) that exist on disk."""
    targets = []
    for entry in EXTRA_PATHS_RAW:
        path = Path(entry['path']).expanduser()
        if path.exists():
            targets.append(IndexTarget(name=entry['name'], path=path, kind=EXTRA))
    return targets


def all_index_targets() -> list[IndexTarget]:
    return load_portfolio_repos() + load_exemplar_repos() + load_extra_paths()


def get_target_by_name(name: str) -> IndexTarget | None:
    """Look up one indexable target by name. Exemplars need their `owner/name` form."""
    for target in all_index_targets():
        if target.name == name:
            return target
    return None


def get_owned_repo_names() -> set[str]:
    return {target.name for target in load_portfolio_repos()}


def get_exemplar_repo_names() -> set[str]:
    return {target.name for target in load_exemplar_repos()}


def is_excluded(relative_path: str, patterns: tuple[str, ...]) -> bool:
    """Gitignore-style matching: patterns apply in order, a leading `!` re-includes,
    and the last matching pattern wins.

    That ordering is what lets a repo drop a whole tree but keep one branch of it.
    FastAPI ships 14 doc translations, so `docs/*/**` then `!docs/en/**` indexes the
    154 English pages and skips the 1501 near-duplicates of them.
    """
    path = PurePosixPath(relative_path)
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith('!')
        candidate = pattern[1:] if negated else pattern
        if path.full_match(candidate):
            excluded = not negated
    return excluded
