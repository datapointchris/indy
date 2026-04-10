"""All meaningful operations: indexing, searching, status.
CLI and MCP are thin wrappers around these functions."""

import hashlib
import os
from datetime import UTC
from datetime import datetime
from pathlib import Path

import httpx

from indy.chunker import chunk_file
from indy.chunker import detect_language
from indy.config import CODE_EXTENSIONS
from indy.config import CONFIG_EXTENSIONS
from indy.config import DOC_EXTENSIONS
from indy.config import MAX_FILE_SIZE_BYTES
from indy.config import OLLAMA_HOST
from indy.config import OLLAMA_MODEL
from indy.config import SKIP_DIRS
from indy.config import SKIP_FILES
from indy.repos import get_repo_by_name
from indy.repos import load_active_repos
from indy.repos import load_extra_paths
from indy.storage import delete_file_chunks
from indy.storage import finish_index_run
from indy.storage import get_chunks_by_symbol
from indy.storage import get_db
from indy.storage import get_error_files
from indy.storage import get_index_stats
from indy.storage import get_indexed_file
from indy.storage import get_indexed_repos
from indy.storage import insert_chunk
from indy.storage import insert_chunk_embedding
from indy.storage import search_chunks
from indy.storage import start_index_run
from indy.storage import upsert_indexed_file

_INDEXABLE_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS


def embed_text(text: str) -> list[float]:
    response = httpx.post(
        f'{OLLAMA_HOST}/api/embeddings',
        json={'model': OLLAMA_MODEL, 'prompt': text},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()['embedding']


def _should_index(path: Path) -> bool:
    return path.suffix.lower() in _INDEXABLE_EXTENSIONS and path.name not in SKIP_FILES and path.stat().st_size <= MAX_FILE_SIZE_BYTES


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def index_path(root: Path, repo_name: str) -> dict:
    """Walk root, embed changed files, update the index manifest. Returns a stats dict."""
    conn = get_db()
    now = datetime.now(UTC).isoformat()
    run_id = start_index_run(conn, repo_name, now)

    files_scanned = 0
    files_updated = 0
    chunks_added = 0
    run_error: str | None = None

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            for filename in filenames:
                filepath = Path(dirpath) / filename

                if not _should_index(filepath):
                    continue

                files_scanned += 1

                try:
                    content = filepath.read_text(encoding='utf-8', errors='ignore')
                    mtime = filepath.stat().st_mtime
                    content_hash = _content_hash(content)

                    existing = get_indexed_file(conn, str(filepath))
                    if existing and existing['content_hash'] == content_hash:
                        continue  # unchanged — skip re-embedding

                    language = detect_language(str(filepath))
                    chunks = chunk_file(str(filepath), content, repo_name)

                    if not chunks:
                        continue

                    delete_file_chunks(conn, str(filepath))

                    file_chunks_added = 0
                    for chunk in chunks:
                        embedding = embed_text(chunk.text)
                        chunk_id = insert_chunk(
                            conn,
                            {
                                'file_path': str(filepath),
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
                            'file_path': str(filepath),
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
                            'file_path': str(filepath),
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


def index_repo(repo_name: str) -> dict:
    repo = get_repo_by_name(repo_name)
    if repo is None:
        raise ValueError(f'Repo {repo_name!r} not found in active repos')
    return index_path(repo['path'], repo_name)


def index_all() -> list[dict]:
    """Index all active repos from repos.json plus configured extra paths."""
    results = [index_path(repo['path'], repo['name']) for repo in load_active_repos()]
    results += [index_path(ep['path'], ep['name']) for ep in load_extra_paths()]
    return results


def search(query: str, repo: str | None = None, language: str | None = None, limit: int = 10) -> list[dict]:
    embedding = embed_text(query)
    conn = get_db()
    try:
        return search_chunks(conn, embedding, repo=repo, language=language, limit=limit)
    finally:
        conn.close()


def search_symbol(name: str, repo: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        return get_chunks_by_symbol(conn, name, repo=repo)
    finally:
        conn.close()


def get_file_content(file_path: str) -> str | None:
    """Read a file directly from disk. Verifies the file exists; returns None if not."""
    path = Path(file_path)
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8', errors='ignore')


def get_status() -> dict:
    conn = get_db()
    try:
        stats = get_index_stats(conn)
        if stats['error_files']:
            stats['error_file_details'] = get_error_files(conn)
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
