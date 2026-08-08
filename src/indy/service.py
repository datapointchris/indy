"""All meaningful operations: indexing, searching, status.
The CLI is a thin wrapper around these functions."""

import hashlib
import sqlite3
import subprocess
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

import httpx

from indy.chunker import chunk_file
from indy.chunker import detect_language
from indy.chunker import extract_references
from indy.config import CODE_EXTENSIONS
from indy.config import CONFIG_EXTENSIONS
from indy.config import CONFIG_PATH
from indy.config import DOC_EXTENSIONS
from indy.config import EXEMPLAR_REPOS_FILE
from indy.config import EXTRA_PATHS_RAW
from indy.config import INDY_DIR
from indy.config import MAX_FILE_SIZE_BYTES
from indy.config import OLLAMA_HOST
from indy.config import OLLAMA_MODEL
from indy.config import REPOS_FILE
from indy.config import SKIP_DIRS
from indy.config import SKIP_FILES
from indy.config import compact_path
from indy.config import expand_path
from indy.config import setting_source
from indy.repos import all_index_targets
from indy.repos import get_exemplar_repo_names
from indy.repos import get_owned_repo_names
from indy.repos import get_target_by_name
from indy.repos import is_excluded
from indy.storage import commit_working_db
from indy.storage import delete_error_files
from indy.storage import delete_file_chunks
from indy.storage import delete_file_references
from indy.storage import discard_working_db
from indy.storage import finish_index_run
from indy.storage import get_chunks_by_symbol
from indy.storage import get_error_files
from indy.storage import get_index_stats
from indy.storage import get_indexed_file
from indy.storage import get_indexed_repos
from indy.storage import get_read_db
from indy.storage import get_recent_runs
from indy.storage import get_repo_scan_history
from indy.storage import get_symbol_callees
from indy.storage import get_symbol_callers
from indy.storage import index_size_bytes as index_size_bytes
from indy.storage import insert_chunk
from indy.storage import insert_chunk_embedding
from indy.storage import insert_references
from indy.storage import open_working_db
from indy.storage import search_chunks
from indy.storage import start_index_run
from indy.storage import upsert_indexed_file

_INDEXABLE_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS


@dataclass(frozen=True)
class IndexProgress:
    """One tick of an in-flight target: how far through it is, and how much was real work.

    A target runs in two passes and this record covers both. While `to_update` is None the
    scan is still hashing files to work out what changed, and `scanned` is the count that
    moves. Once it is known, `updated` moves against it and is the only number that reflects
    real work — on a re-index everything is scanned and almost nothing is re-embedded.
    """

    scanned: int
    updated: int
    to_update: int | None
    total: int
    current_file: str


ProgressCallback = Callable[[IndexProgress], None]


def embed_text(text: str) -> list[float]:
    response = httpx.post(
        f'{OLLAMA_HOST}/api/embed',
        json={'model': OLLAMA_MODEL, 'input': text},
        timeout=30.0,
    )
    if response.status_code != 200:
        try:
            body = response.json().get('error', response.text)
        except Exception:
            body = response.text
        raise RuntimeError(f'embedding failed ({response.status_code}): {body}')
    return response.json()['embeddings'][0]


def should_index(path: Path) -> bool:
    return path.suffix.lower() in _INDEXABLE_EXTENSIONS and path.name not in SKIP_FILES and path.stat().st_size <= MAX_FILE_SIZE_BYTES


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def is_in_skip_dir(filepath: Path, root: Path) -> bool:
    """Check if any path component between root and filepath is in SKIP_DIRS."""
    return bool(SKIP_DIRS & set(filepath.relative_to(root).parts))


def list_git_files(root: Path) -> list[str] | None:
    """Return relative paths from git (tracked + untracked, excluding gitignored).
    Returns None if root is not inside a git repo."""
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()


def collect_indexable_files(root: Path, exclude: tuple[str, ...] = ()) -> list[Path]:
    """Collect all files to index under root, respecting .gitignore for git repos.

    `exclude` holds the target's own gitignore-style patterns, applied on top of the
    global SKIP_DIRS. Per-repo patterns are what a global set cannot express: a repo
    whose docs tree is 91% translations needs that tree dropped without dropping docs
    everywhere else.
    """
    git_files = list_git_files(root)
    if git_files is not None:
        files = []
        for rel_path in git_files:
            filepath = root / rel_path
            if is_excluded(rel_path, exclude):
                continue
            if filepath.exists() and not is_in_skip_dir(filepath, root) and should_index(filepath):
                files.append(filepath)
        return files

    files = []
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            filepath = dirpath / filename
            if is_excluded(str(filepath.relative_to(root)), exclude):
                continue
            if should_index(filepath):
                files.append(filepath)
    return files


class IndexSession:
    """One invocation of indexing, however many targets it covers.

    Everything written during the session goes to a working copy, and the whole of it
    becomes the index in a single rename when the session closes. Held open across every
    target rather than per target because the swap costs a copy of the database: doing it
    once a run is the difference between one copy and seventy.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def index_path(
        self,
        root: Path,
        repo_name: str,
        on_progress: ProgressCallback | None = None,
        exclude: tuple[str, ...] = (),
    ) -> dict:
        return index_into(self._conn, root, repo_name, on_progress=on_progress, exclude=exclude)

    def clear_errors(self) -> int:
        return delete_error_files(self._conn)


@contextmanager
def index_session(on_stage: Callable[[str], None] | None = None) -> Iterator[IndexSession]:
    """Open a working copy, yield a session that writes to it, and swap it in on success.

    A run that raises leaves the index exactly as it was — the working copy is thrown away
    and indy.db was never opened for writing in the first place.

    on_stage is called with 'copying' before the seed copy and 'swapping' before the rename.
    Both block for as long as a database of this size takes to move, and a CLI that says
    nothing during them looks hung.
    """
    if on_stage:
        on_stage('copying')
    conn = open_working_db()
    try:
        yield IndexSession(conn)
    except BaseException:
        discard_working_db(conn)
        raise
    if on_stage:
        on_stage('swapping')
    commit_working_db(conn)


def index_into(
    conn: sqlite3.Connection,
    root: Path,
    repo_name: str,
    on_progress: ProgressCallback | None = None,
    exclude: tuple[str, ...] = (),
) -> dict:
    """Walk root, embed changed files, update the index manifest. Returns a stats dict.

    on_progress is called before each file and once more when the target finishes, so the
    final tick reports the last file's outcome rather than stopping one short of it.
    """
    indexable_files = collect_indexable_files(root, exclude)
    total_files = len(indexable_files)
    now = datetime.now(UTC).isoformat()
    run_id = start_index_run(conn, repo_name, now)

    files_scanned = 0
    files_updated = 0
    chunks_added = 0
    to_update: int | None = None
    run_error: str | None = None

    def report(current_file: str) -> None:
        if on_progress:
            on_progress(
                IndexProgress(
                    scanned=files_scanned,
                    updated=files_updated,
                    to_update=to_update,
                    total=total_files,
                    current_file=current_file,
                )
            )

    try:
        # Hashing every file before embedding any of them is what lets the run state how much
        # work it has, rather than only how many files it has walked past. On a re-index the
        # two are nothing alike: everything is read and hashed, and almost none of it is
        # re-embedded. Files needing work are read a second time below, which costs one extra
        # pass over the ones that changed and is invisible beside the cost of embedding them.
        pending = []
        for filepath in indexable_files:
            files_scanned += 1
            report(filepath.name)
            try:
                content_hash = compute_content_hash(filepath.read_text(encoding='utf-8', errors='ignore'))
            except OSError:
                pending.append(filepath)  # let the work pass fail on it and record why
                continue
            existing = get_indexed_file(conn, compact_path(str(filepath)))
            if existing and existing['content_hash'] == content_hash:
                continue  # unchanged — skip re-embedding
            pending.append(filepath)

        to_update = len(pending)
        report('')

        for filepath in pending:
            report(filepath.name)
            db_path = compact_path(str(filepath))

            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')
                mtime = filepath.stat().st_mtime
                content_hash = compute_content_hash(content)

                language = detect_language(str(filepath))
                chunks = chunk_file(db_path, content, repo_name)

                if not chunks:
                    continue

                delete_file_chunks(conn, db_path)
                delete_file_references(conn, db_path)

                refs = extract_references(db_path, content)
                if refs:
                    insert_references(conn, [asdict(r) for r in refs])

                file_chunks_added = 0
                for chunk in chunks:
                    embedding = embed_text(chunk.text)
                    chunk_id = insert_chunk(
                        conn,
                        {
                            'file_path': db_path,
                            'repo': repo_name,
                            'language': language,
                            'symbol_name': chunk.symbol_name,
                            'symbol_type': chunk.symbol_type,
                            'module': chunk.module,
                            'text': chunk.text,
                        },
                    )
                    insert_chunk_embedding(conn, chunk_id, embedding)
                    file_chunks_added += 1

                upsert_indexed_file(
                    conn,
                    {
                        'repo': repo_name,
                        'file_path': db_path,
                        'language': language,
                        'mtime': mtime,
                        'content_hash': content_hash,
                        'chunk_count': file_chunks_added,
                        'indexed_at': datetime.now(UTC).isoformat(),
                        'status': 'ok',
                    },
                )
                conn.commit()
                files_updated += 1
                chunks_added += file_chunks_added

            except Exception as exc:
                upsert_indexed_file(
                    conn,
                    {
                        'repo': repo_name,
                        'file_path': db_path,
                        'language': detect_language(str(filepath)),
                        'mtime': filepath.stat().st_mtime if filepath.exists() else 0.0,
                        'content_hash': '',
                        'chunk_count': 0,
                        'indexed_at': datetime.now(UTC).isoformat(),
                        'status': f'error: {exc}',
                    },
                )
                conn.commit()

    except Exception as exc:
        run_error = str(exc)

    finally:
        report('')
        finish_index_run(
            conn,
            run_id,
            {
                'finished_at': datetime.now(UTC).isoformat(),
                'files_scanned': files_scanned,
                'files_updated': files_updated,
                'chunks_added': chunks_added,
                'error': run_error,
            },
        )
        conn.commit()

    return {
        'repo': repo_name,
        'files_scanned': files_scanned,
        'files_updated': files_updated,
        'chunks_added': chunks_added,
        'error': run_error,
    }


def index_path(
    root: Path,
    repo_name: str,
    on_progress: ProgressCallback | None = None,
    exclude: tuple[str, ...] = (),
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Index one path as a session of its own."""
    with index_session(on_stage=on_stage) as session:
        return session.index_path(root, repo_name, on_progress=on_progress, exclude=exclude)


def index_repo(
    repo_name: str,
    on_progress: ProgressCallback | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    target = get_target_by_name(repo_name)
    if target is None:
        raise ValueError(f'Repo {repo_name!r} not found in active repos')
    return index_path(target.path, target.name, on_progress=on_progress, exclude=target.exclude, on_stage=on_stage)


def index_all(
    on_progress: ProgressCallback | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> list[dict]:
    """Index every target: portfolio repos, exemplar clones, and configured extra paths."""
    with index_session(on_stage=on_stage) as session:
        return [
            session.index_path(target.path, target.name, on_progress=on_progress, exclude=target.exclude) for target in all_index_targets()
        ]


def expand_result_paths(results: list[dict], key: str = 'file_path') -> list[dict]:
    """Expand ~-prefixed paths in a list of result dicts back to absolute paths."""
    for r in results:
        if key in r:
            r[key] = expand_path(r[key])
    return results


def resolve_ownership_filter(owned: bool | None) -> set[str] | None:
    """Translate owned flag into a set of repo names to include, or None for no filter."""
    if owned is None:
        return None
    if owned:
        return get_owned_repo_names()
    return get_exemplar_repo_names()


def search(
    query: str,
    repo: str | None = None,
    language: str | None = None,
    limit: int = 10,
    owned: bool | None = None,
) -> list[dict]:
    repos = resolve_ownership_filter(owned)
    embedding = embed_text(query)
    conn = get_read_db()
    try:
        return expand_result_paths(search_chunks(conn, embedding, repo=repo, language=language, limit=limit, repos=repos))
    finally:
        conn.close()


def search_symbol(name: str, repo: str | None = None) -> list[dict]:
    conn = get_read_db()
    try:
        return expand_result_paths(get_chunks_by_symbol(conn, name, repo=repo))
    finally:
        conn.close()


def get_status() -> dict:
    conn = get_read_db()
    try:
        stats = get_index_stats(conn)
        stats['recent_runs'] = get_recent_runs(conn, limit=5)
        if stats['error_files']:
            stats['error_file_details'] = expand_result_paths(get_error_files(conn))
        else:
            stats['error_file_details'] = []
        return stats
    finally:
        conn.close()


def list_repos() -> list[dict]:
    conn = get_read_db()
    try:
        return get_indexed_repos(conn)
    finally:
        conn.close()


def repo_scan_history(repo: str, limit: int = 20) -> list[dict]:
    conn = get_read_db()
    try:
        return get_repo_scan_history(conn, repo, limit=limit)
    finally:
        conn.close()


def refresh(repo: str | None = None) -> dict:
    """Incremental re-index. Only re-embeds files whose content hash changed."""
    if repo:
        result = index_repo(repo)
        return result
    results = index_all()
    return {
        'repos_refreshed': len(results),
        'files_updated': sum(r['files_updated'] for r in results),
        'chunks_added': sum(r['chunks_added'] for r in results),
        'errors': [r['error'] for r in results if r['error']],
    }


def clear_errors(on_stage: Callable[[str], None] | None = None) -> int:
    """Remove all error records from indexed_file. Returns count cleared.

    Goes through a session like any other write. Copying the whole database to delete a
    handful of rows is disproportionate work for the size of the change, and it is the
    price of the invariant that makes the index safe to replicate: indy.db is only ever
    replaced, so there is no write path that leaves it open and half-modified.
    """
    with index_session(on_stage=on_stage) as session:
        return session.clear_errors()


def get_dependencies(symbol_name: str, repo: str | None = None, direction: str = 'both') -> dict:
    """Return callers and/or callees of a symbol from the reference table.

    direction='callers' → what calls symbol_name
    direction='callees' → what symbol_name calls
    direction='both'    → both
    """
    conn = get_read_db()
    try:
        result: dict = {}
        if direction in ('callers', 'both'):
            result['callers'] = expand_result_paths(get_symbol_callers(conn, symbol_name, repo=repo), key='source_file')
        if direction in ('callees', 'both'):
            result['callees'] = expand_result_paths(get_symbol_callees(conn, symbol_name, repo=repo), key='source_file')
        return result
    finally:
        conn.close()


def describe_setting(name: str, value: str, env_var: str, key: str) -> dict:
    """One resolved setting: its value, the layer that supplied it, and whether it is there.

    `exists` is only meaningful for a path, so a non-path setting reports True rather than
    claiming a URL is missing.
    """
    is_path = value.startswith(('/', '~'))
    return {
        'name': name,
        'value': value,
        'source': setting_source(env_var, key),
        'exists': Path(value).expanduser().exists() if is_path else True,
    }


def describe_config() -> dict:
    """Every resolved setting, the layer it came from, and whether it is actually there.

    Reports paths rather than validating them: a registry indy has not been pointed at
    yet is the normal state of a fresh install, not an error.
    """
    settings = [
        describe_setting('data_dir', str(INDY_DIR), 'INDY_DIR', 'data_dir'),
        describe_setting('repos_file', str(REPOS_FILE), 'INDY_REPOS_FILE', 'repos_file'),
        describe_setting('exemplar_repos_file', str(EXEMPLAR_REPOS_FILE), 'INDY_EXEMPLAR_REPOS_FILE', 'exemplar_repos_file'),
        describe_setting('ollama_host', OLLAMA_HOST, 'OLLAMA_HOST', 'ollama_host'),
        describe_setting('ollama_model', OLLAMA_MODEL, 'OLLAMA_MODEL', 'ollama_model'),
    ]
    return {
        'config_file': str(CONFIG_PATH),
        'config_file_exists': CONFIG_PATH.exists(),
        'settings': settings,
        'extra_paths': [
            {'name': entry['name'], 'path': entry['path'], 'exists': Path(entry['path']).expanduser().exists()} for entry in EXTRA_PATHS_RAW
        ],
    }
