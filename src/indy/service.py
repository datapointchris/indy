"""All meaningful operations: indexing, searching, status.
CLI and MCP are thin wrappers around these functions."""

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from pathlib import Path

import httpx

from indy.chunker import chunk_file
from indy.chunker import detect_language
from indy.chunker import extract_references
from indy.config import CODE_EXTENSIONS
from indy.config import CONFIG_EXTENSIONS
from indy.config import DOC_EXTENSIONS
from indy.config import MAX_FILE_SIZE_BYTES
from indy.config import OLLAMA_HOST
from indy.config import OLLAMA_MODEL
from indy.config import SKIP_DIRS
from indy.config import SKIP_FILES
from indy.config import compact_path
from indy.config import expand_path
from indy.repos import get_repo_by_name
from indy.repos import load_active_repos
from indy.repos import load_extra_paths
from indy.storage import delete_error_files
from indy.storage import delete_file_chunks
from indy.storage import delete_file_references
from indy.storage import finish_index_run
from indy.storage import get_chunks_by_symbol
from indy.storage import get_db
from indy.storage import get_error_files
from indy.storage import get_index_stats
from indy.storage import get_indexed_file
from indy.storage import get_indexed_repos
from indy.storage import get_recent_runs
from indy.storage import get_repo_scan_history
from indy.storage import get_symbol_callees
from indy.storage import get_symbol_callers
from indy.storage import insert_chunk
from indy.storage import insert_chunk_embedding
from indy.storage import insert_references
from indy.storage import search_chunks
from indy.storage import start_index_run
from indy.storage import upsert_indexed_file

_INDEXABLE_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS


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


def _should_index(path: Path) -> bool:
    return path.suffix.lower() in _INDEXABLE_EXTENSIONS and path.name not in SKIP_FILES and path.stat().st_size <= MAX_FILE_SIZE_BYTES


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _is_in_skip_dir(filepath: Path, root: Path) -> bool:
    """Check if any path component between root and filepath is in SKIP_DIRS."""
    return bool(SKIP_DIRS & set(filepath.relative_to(root).parts))


def _list_git_files(root: Path) -> list[str] | None:
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


def collect_indexable_files(root: Path) -> list[Path]:
    """Collect all files to index under root, respecting .gitignore for git repos."""
    git_files = _list_git_files(root)
    if git_files is not None:
        files = []
        for rel_path in git_files:
            filepath = root / rel_path
            if filepath.exists() and not _is_in_skip_dir(filepath, root) and _should_index(filepath):
                files.append(filepath)
        return files

    files = []
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            filepath = dirpath / filename
            if _should_index(filepath):
                files.append(filepath)
    return files


def index_path(root: Path, repo_name: str, on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Walk root, embed changed files, update the index manifest. Returns a stats dict.

    on_progress(files_scanned, total_files, current_file) is called before processing each file.
    """
    indexable_files = collect_indexable_files(root)
    total_files = len(indexable_files)
    conn = get_db()
    now = datetime.now(UTC).isoformat()
    run_id = start_index_run(conn, repo_name, now)

    files_scanned = 0
    files_updated = 0
    chunks_added = 0
    run_error: str | None = None

    try:
        for filepath in indexable_files:
            files_scanned += 1
            if on_progress:
                on_progress(files_scanned, total_files, filepath.name)
            db_path = compact_path(str(filepath))

            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')
                mtime = filepath.stat().st_mtime
                content_hash = _content_hash(content)

                existing = get_indexed_file(conn, db_path)
                if existing and existing['content_hash'] == content_hash:
                    continue  # unchanged — skip re-embedding

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
        conn.close()

    return {
        'repo': repo_name,
        'files_scanned': files_scanned,
        'files_updated': files_updated,
        'chunks_added': chunks_added,
        'error': run_error,
    }


def index_repo(repo_name: str, on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    repo = get_repo_by_name(repo_name)
    if repo is None:
        raise ValueError(f'Repo {repo_name!r} not found in active repos')
    return index_path(repo['path'], repo_name, on_progress=on_progress)


def index_all(on_progress: Callable[[int, int, str], None] | None = None) -> list[dict]:
    """Index all active repos from repos.json plus configured extra paths."""
    results = [index_path(repo['path'], repo['name'], on_progress=on_progress) for repo in load_active_repos()]
    results += [index_path(ep['path'], ep['name'], on_progress=on_progress) for ep in load_extra_paths()]
    return results


def expand_result_paths(results: list[dict], key: str = 'file_path') -> list[dict]:
    """Expand ~-prefixed paths in a list of result dicts back to absolute paths."""
    for r in results:
        if key in r:
            r[key] = expand_path(r[key])
    return results


def search(query: str, repo: str | None = None, language: str | None = None, limit: int = 10) -> list[dict]:
    embedding = embed_text(query)
    conn = get_db()
    try:
        return expand_result_paths(search_chunks(conn, embedding, repo=repo, language=language, limit=limit))
    finally:
        conn.close()


def search_symbol(name: str, repo: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        return expand_result_paths(get_chunks_by_symbol(conn, name, repo=repo))
    finally:
        conn.close()


def get_file_content(file_path: str) -> str | None:
    """Read a file directly from disk. Verifies the file exists; returns None if not."""
    path = Path(expand_path(file_path))
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8', errors='ignore')


def get_status() -> dict:
    conn = get_db()
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
    conn = get_db()
    try:
        return get_indexed_repos(conn)
    finally:
        conn.close()


def repo_scan_history(repo: str, limit: int = 20) -> list[dict]:
    conn = get_db()
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


def clear_errors() -> int:
    """Remove all error records from indexed_file. Returns count cleared."""
    conn = get_db()
    try:
        return delete_error_files(conn)
    finally:
        conn.close()


def get_dependencies(symbol_name: str, repo: str | None = None, direction: str = 'both') -> dict:
    """Return callers and/or callees of a symbol from the reference table.

    direction='callers' → what calls symbol_name
    direction='callees' → what symbol_name calls
    direction='both'    → both
    """
    conn = get_db()
    try:
        result: dict = {}
        if direction in ('callers', 'both'):
            result['callers'] = expand_result_paths(get_symbol_callers(conn, symbol_name, repo=repo), key='source_file')
        if direction in ('callees', 'both'):
            result['callees'] = expand_result_paths(get_symbol_callees(conn, symbol_name, repo=repo), key='source_file')
        return result
    finally:
        conn.close()
