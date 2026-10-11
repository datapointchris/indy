"""All meaningful operations: indexing, searching, status.
The CLI is a thin wrapper around these functions."""

import datetime as dt
import filecmp
import hashlib
import heapq
import json
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterator
from contextlib import closing
from contextlib import contextmanager
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

import httpx2

from indy.chunker import EXTRACTOR_VERSION
from indy.chunker import chunk_file
from indy.chunker import detect_language
from indy.chunker import extract_references
from indy.chunker import shebang_language
from indy.config import CODE_EXTENSIONS
from indy.config import CODE_FILENAMES
from indy.config import CONFIG_EXTENSIONS
from indy.config import CONFIG_PATH
from indy.config import DOC_EXTENSIONS
from indy.config import EXEMPLAR_REGISTRY
from indy.config import EXTRA_PATHS_RAW
from indy.config import INDY_DIR
from indy.config import MAX_FILE_SIZE_BYTES
from indy.config import OLLAMA_HOST
from indy.config import OLLAMA_MODEL
from indy.config import REPOS_REGISTRY
from indy.config import SKIP_DIRS
from indy.config import SKIP_FILES
from indy.config import compact_path
from indy.config import expand_path
from indy.config import setting_source
from indy.repos import EXEMPLAR
from indy.repos import OWNED
from indy.repos import IndexTarget
from indy.repos import all_index_targets
from indy.repos import get_exemplar_repo_names
from indy.repos import get_owned_repo_names
from indy.repos import get_target_by_name
from indy.repos import is_excluded
from indy.scip import SCIP_LINK_VERSION
from indy.scip import ScipError
from indy.scip import describe_environment
from indy.scip import environment_digest
from indy.scip import link_documents
from indy.scip import lockfile_digest
from indy.scip import name_symbol
from indy.scip import node_installed
from indy.scip import run_scip_python
from indy.scip import venv_python
from indy.storage import IndexNotFoundError
from indy.storage import base_stamp
from indy.storage import clear_overlay_file
from indy.storage import commit_working_db
from indy.storage import compact_overlay
from indy.storage import delete_error_files
from indy.storage import delete_file_chunks
from indy.storage import delete_file_references
from indy.storage import delete_indexed_file
from indy.storage import delete_overlay_label
from indy.storage import delete_repo
from indy.storage import discard_working_db
from indy.storage import finish_index_run
from indy.storage import get_chunk_vectors
from indy.storage import get_chunks_by_symbol
from indy.storage import get_error_files
from indy.storage import get_file_states
from indy.storage import get_index_stats
from indy.storage import get_indexed_file
from indy.storage import get_indexed_repos
from indy.storage import get_last_scans
from indy.storage import get_member_callers
from indy.storage import get_overlay_paths
from indy.storage import get_overlay_summary
from indy.storage import get_paths_extracted_by_another_version
from indy.storage import get_python_file_hashes
from indy.storage import get_read_db
from indy.storage import get_recent_runs
from indy.storage import get_reconciled_stamp
from indy.storage import get_repo_file_paths
from indy.storage import get_repo_scan_history
from indy.storage import get_scip_call_sites
from indy.storage import get_scip_call_targets
from indy.storage import get_scip_documents
from indy.storage import get_scip_overridden
from indy.storage import get_scip_references
from indy.storage import get_scip_state
from indy.storage import get_scip_states
from indy.storage import get_scip_targets
from indy.storage import get_symbol_callees
from indy.storage import get_symbol_callers
from indy.storage import get_tombstone
from indy.storage import get_tombstoned_paths
from indy.storage import has_scip_tables
from indy.storage import index_size_bytes as index_size_bytes
from indy.storage import insert_chunk
from indy.storage import insert_chunk_embedding
from indy.storage import insert_overlay_file
from indy.storage import insert_references
from indy.storage import is_indexed_class
from indy.storage import mark_overlay_updated
from indy.storage import open_base_readonly
from indy.storage import open_overlay_db
from indy.storage import open_working_db
from indy.storage import overlay_path
from indy.storage import put_tombstone
from indy.storage import replace_scip_index
from indy.storage import search_chunks
from indy.storage import set_extractor_version
from indy.storage import set_overlay_branch
from indy.storage import start_index_run
from indy.storage import upsert_indexed_file
from indy.watch import WorkQueue
from indy.watch import watch_changes

INDEXABLE_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS


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
    response = httpx2.post(
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


def read_first_line(path: Path) -> str:
    """As much of a file's opening line as a shebang occupies, or nothing for one that cannot be read."""
    try:
        with path.open('rb') as handle:
            return handle.readline(256).decode('utf-8', errors='ignore')
    except OSError:
        return ''


def should_index(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    if path.suffix:
        wanted = path.suffix.lower() in INDEXABLE_EXTENSIONS
    else:
        wanted = path.name in CODE_FILENAMES or shebang_language(read_first_line(path)) is not None
    return wanted and path.stat().st_size <= MAX_FILE_SIZE_BYTES


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
        self.indexed_labels: set[str] = set()

    def index_path(
        self,
        root: Path,
        repo_name: str,
        on_progress: ProgressCallback | None = None,
        exclude: tuple[str, ...] = (),
    ) -> dict:
        self.indexed_labels.add(repo_name)
        return index_into(self._conn, root, repo_name, on_progress=on_progress, exclude=exclude)

    def index_target(self, target: IndexTarget, on_progress: ProgressCallback | None = None) -> dict:
        """Index one target. An owned repo's Python is then resolved by scip-python, against the files this run just read.

        An interrupt anywhere in an owned repo drops its SCIP rows before the session swaps the
        working copy in. They describe the files as they were before this run, and the run may
        already have replaced some of those.
        """
        if target.kind != OWNED:
            return self.index_path(target.path, target.name, on_progress=on_progress, exclude=target.exclude)
        try:
            result = self.index_path(target.path, target.name, on_progress=on_progress, exclude=target.exclude)

            def announce() -> None:
                if on_progress:
                    scanned, updated = result['files_scanned'], result['files_updated']
                    on_progress(
                        IndexProgress(scanned=scanned, updated=updated, to_update=updated, total=scanned, current_file='scip-python')
                    )

            result['scip'] = resolve_python_symbols(self._conn, target.path, target.name, on_start=announce)
        except KeyboardInterrupt:
            replace_scip_index(self._conn, scip_state(target.name, 'skipped', INTERRUPTED_REASON))
            raise
        return result

    def clear_errors(self) -> int:
        return delete_error_files(self._conn)

    def forget_repo(self, repo: str) -> dict:
        return delete_repo(self._conn, repo)


@contextmanager
def index_session(on_stage: Callable[[str], None] | None = None) -> Iterator[IndexSession]:
    """Open a working copy, yield a session that writes to it, and swap it in when it ends.

    An interrupt swaps in what the run managed, rather than discarding it. Every file is
    committed as it is embedded, so the working copy is a complete database at every moment
    — throwing it away would mean a run stopped at hour three had done nothing at all, which
    is what building into a copy cost until this was handled. Ctrl-C is the ordinary way to
    stop a long index, not an error.

    A failure to open or commit is different: nothing partial is left behind, and the index
    stays exactly as it was, having never been opened for writing.

    on_stage is called with 'snapshot' before the run takes its copy and 'save' before that
    copy becomes the index. Both block for as long as a database of this size takes to move,
    and a CLI that says nothing during them looks hung. They are named for what the run is
    doing rather than for the copy and rename that implement it, since the caller announcing
    them is writing for someone watching an index, not reading this file.

    Once the new index is in place, the overlay drops every file it now matches. Under the
    labels this session indexed that includes a file read at the same or a later mtime, since
    this machine just read those files itself.
    """
    if on_stage:
        on_stage('snapshot')
    conn = open_working_db()
    session = IndexSession(conn)
    try:
        yield session
    except KeyboardInterrupt:
        commit_working_db(conn)
        compact_live_overlay(session.indexed_labels)
        raise
    except BaseException:
        discard_working_db(conn)
        raise
    if on_stage:
        on_stage('save')
    commit_working_db(conn)
    compact_live_overlay(session.indexed_labels)


def prune_missing_files(conn: sqlite3.Connection, repo_name: str, root: Path) -> int:
    """Drop manifest rows under root whose file is gone from disk. Returns the count.

    A file deleted or renamed between runs leaves its row, its chunks and its vectors
    behind, and no later run can clear them: indexing only ever visits files that exist,
    so nothing revisits the path that stopped existing. Search kept returning those
    chunks under a path that resolves to nothing.

    Scoped to paths under this run's root rather than to the whole label, because a label
    can be produced by more than one root — `index --path` names a target after its
    directory — and a run that walked one of them must not delete another's rows.

    Absence from disk, not absence from the walk. The two look equivalent and are not: a
    walk stops at a symlinked subtree, since Path.walk does not follow one, so pruning
    what the walk missed deletes files that are still there. `~/dev/standards` became a
    symlink and took the fleet's standards out of the index on the next run. The cost of
    the narrow rule is that a file newly covered by an exclude pattern keeps its rows
    until it is deleted, which leaves stale content rather than losing live content.
    """
    prefix = compact_path(str(root)).rstrip('/')
    stale = [
        path for path in get_repo_file_paths(conn, repo_name) if path.startswith(f'{prefix}/') and not Path(expand_path(path)).exists()
    ]
    for path in stale:
        delete_file_chunks(conn, path)
        delete_file_references(conn, path)
        delete_indexed_file(conn, path)
    conn.commit()
    return len(stale)


def replace_references(conn: sqlite3.Connection, db_path: str, content: str) -> None:
    """Swap a file's symbol_reference rows for ones extracted from content."""
    delete_file_references(conn, db_path)
    refs = extract_references(db_path, content)
    if refs:
        insert_references(conn, [asdict(r) for r in refs])


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
    now = dt.datetime.now(dt.UTC).isoformat()
    run_id = start_index_run(conn, repo_name, now)

    files_scanned = 0
    files_updated = 0
    chunks_added = 0
    files_pruned = 0
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
        #
        # An unchanged file is never re-embedded, so it would also never reach a newer reference
        # extractor. Its references are re-extracted here from the content already read, leaving
        # its chunks and vectors alone.
        pending = []
        for filepath in indexable_files:
            files_scanned += 1
            report(filepath.name)
            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                pending.append(filepath)  # let the work pass fail on it and record why
                continue
            db_path = compact_path(str(filepath))
            existing = get_indexed_file(conn, db_path)
            if existing and existing['content_hash'] == compute_content_hash(content):
                if existing['extractor_version'] != EXTRACTOR_VERSION:
                    replace_references(conn, db_path, content)
                    set_extractor_version(conn, db_path, EXTRACTOR_VERSION)
                continue
            pending.append(filepath)
        conn.commit()

        to_update = len(pending)
        report('')

        for filepath in pending:
            report(filepath.name)
            db_path = compact_path(str(filepath))
            content = ''

            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')
                mtime = filepath.stat().st_mtime
                content_hash = compute_content_hash(content)

                language = detect_language(str(filepath), content)
                chunks = chunk_file(db_path, content, repo_name)

                # A file that chunks to nothing still gets its manifest row, so its old chunks go
                # and the next run skips it, and the overlay can find this content in the base.
                delete_file_chunks(conn, db_path)
                replace_references(conn, db_path, content)

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
                        'indexed_at': dt.datetime.now(dt.UTC).isoformat(),
                        'status': 'ok',
                        'extractor_version': EXTRACTOR_VERSION,
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
                        'language': detect_language(str(filepath), content),
                        'mtime': filepath.stat().st_mtime if filepath.exists() else 0.0,
                        'content_hash': '',
                        'chunk_count': 0,
                        'indexed_at': dt.datetime.now(dt.UTC).isoformat(),
                        'status': f'error: {exc}',
                        'extractor_version': 0,
                    },
                )
                conn.commit()

        # Only against a walk that found something. An unmounted or moved root makes every
        # path missing at once, which would empty the label; a walk that found nothing is
        # the cheap signal for that, and it costs only the case where a target's every file
        # really was deleted. `indy forget` clears a label that really did go.
        if indexable_files:
            files_pruned = prune_missing_files(conn, repo_name, root)

    except Exception as exc:
        run_error = str(exc)

    finally:
        report('')
        finish_index_run(
            conn,
            run_id,
            {
                'finished_at': dt.datetime.now(dt.UTC).isoformat(),
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
        'files_pruned': files_pruned,
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
    with index_session(on_stage=on_stage) as session:
        return session.index_target(target, on_progress=on_progress)


def index_all(
    on_progress: ProgressCallback | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> list[dict]:
    """Index every target: portfolio repos, exemplar clones, and configured extra paths."""
    with index_session(on_stage=on_stage) as session:
        return [session.index_target(target, on_progress=on_progress) for target in all_index_targets()]


# ── scip-python ───────────────────────────────────────────────────────────────

NO_VENV_REASON = 'no .venv, and scip-python needs the project installed in one to name its modules'
NO_NODE_REASON = 'Node.js is not installed, and scip-python runs on it: no node, npm or npx on PATH'
INTERRUPTED_REASON = 'the index was interrupted before scip-python finished'


def scip_fingerprint(files: list[tuple[str, str]], environment: str) -> str:
    """What a scip-python run over a repo depends on: its Python files, its packages, scip-python's own, and the code reading the result."""
    inputs = [lockfile_digest(), SCIP_LINK_VERSION, EXTRACTOR_VERSION, environment_digest(environment), files]
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


def scip_state(repo_name: str, status: str, reason: str | None = None) -> dict:
    """A scip_index record with no rows behind it."""
    return {
        'repo': repo_name,
        'indexed_at': dt.datetime.now(dt.UTC).isoformat(),
        'status': status,
        'reason': reason,
        'fingerprint': None,
        'document_count': 0,
        'reference_count': 0,
        'seconds': None,
    }


def resolve_python_symbols(
    conn: sqlite3.Connection,
    root: Path,
    repo_name: str,
    on_start: Callable[[], None] | None = None,
) -> dict:
    """Run scip-python over one repo and replace its scip_* rows. Returns the repo's scip_index record.

    A repo it cannot index is recorded as skipped or failed, with the reason, and keeps no
    rows. That never stops the run, and `deps` answers for the repo from its syntactic rows.

    The run is skipped when nothing it depends on changed since the last one that succeeded.
    A Python file indy does not index is outside that check, as it is outside the links.
    """
    started = time.perf_counter()
    state = scip_state(repo_name, 'skipped')
    try:
        files = get_python_file_hashes(conn, repo_name)
        python = venv_python(root)
        if not files:
            state['reason'] = 'no Python files'
        elif python is None:
            state['reason'] = NO_VENV_REASON
        elif not node_installed():
            state['reason'] = NO_NODE_REASON
        else:
            environment = describe_environment(python)
            fingerprint = scip_fingerprint(files, environment)
            previous = get_scip_state(conn, repo_name)
            if previous and previous['status'] == 'ok' and previous['fingerprint'] == fingerprint:
                return {**previous, 'unchanged': True}
            if on_start:
                on_start()
            documents = run_scip_python(root, repo_name, environment)
            linked = link_documents(root, repo_name, documents, {path for path, _ in files})
            state |= {
                'status': 'ok',
                'fingerprint': fingerprint,
                'document_count': len(linked.documents),
                'reference_count': len(linked.references),
                'seconds': round(time.perf_counter() - started, 1),
            }
            rows = {
                'scip_document': linked.documents,
                'scip_symbol': linked.symbols,
                'scip_occurrence': linked.references,
                'scip_relationship': linked.relationships,
            }
            replace_scip_index(conn, state, rows)
            return state
    except Exception as exc:
        state |= {'status': 'failed', 'reason': str(exc) if isinstance(exc, ScipError) else f'{type(exc).__name__}: {exc}'}
    state['seconds'] = round(time.perf_counter() - started, 1)
    replace_scip_index(conn, state)
    return state


# ── live overlay ──────────────────────────────────────────────────────────────

# Each warm request asks ollama to keep the model loaded for ten minutes, so a ping every
# four keeps it resident with margin.
KEEP_WARM_SECONDS = 240

BASE_POLL_SECONDS = 2.0

# A file whose update raised is tried again after this, doubling each time, until the wait
# would pass RETRY_LAST_SECONDS. An ollama restart is the case it is for.
RETRY_FIRST_SECONDS = 2.0
RETRY_LAST_SECONDS = 300.0


@dataclass(frozen=True)
class LiveRoot:
    """A directory `indy watch` keeps current, and the label its files index under.

    A linked worktree carries its branch and the main checkout it belongs to. Its files index
    under the repo's own label, and only where they differ from the main checkout's: an
    unchanged worktree file is the same code, and indexing it would return every hit twice.
    """

    path: Path
    label: str
    exclude: tuple[str, ...] = ()
    branch: str | None = None
    checkout: Path | None = None


@dataclass(frozen=True)
class LiveChange:
    """What updating one file did to the overlay.

    action is 'indexed', 'removed', or 'restored' when the file is back to the content a read
    would get without the overlay, so the overlay stops holding it.
    """

    file_path: str
    label: str
    branch: str | None
    action: str
    embedded: int = 0
    reused: int = 0
    seconds: float = 0.0


def list_worktrees(checkout: Path) -> list[tuple[Path, str]]:
    """A checkout's linked worktrees, each with its branch. Empty for anything that is not a git repo."""
    result = subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=checkout, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    main = checkout.resolve()
    worktrees = []
    for block in result.stdout.split('\n\n'):
        fields = {key: value for key, _, value in (line.partition(' ') for line in block.splitlines())}
        path = Path(fields.get('worktree', ''))
        if not path.is_absolute() or 'bare' in fields or 'prunable' in fields or path.resolve() == main or not path.is_dir():
            continue
        worktrees.append((path, fields['branch'].removeprefix('refs/heads/') if 'branch' in fields else 'detached'))
    return worktrees


def live_roots() -> list[LiveRoot]:
    """Every root `indy watch` keeps current: each target but the exemplar clones, and each owned repo's worktrees.

    Exemplars change only when pulled, which a full `indy index` covers.
    """
    roots = []
    for target in all_index_targets():
        if target.kind == EXEMPLAR:
            continue
        roots.append(LiveRoot(target.path, target.name, target.exclude))
        if target.kind == OWNED:
            roots += [LiveRoot(path, target.name, target.exclude, branch, target.path) for path, branch in list_worktrees(target.path)]
    return roots


def root_for(path: Path, roots: list[LiveRoot]) -> LiveRoot | None:
    """The innermost root holding path."""
    return max((root for root in roots if path.is_relative_to(root.path)), key=lambda root: len(root.path.parts), default=None)


def is_worktree_event(path: Path) -> bool:
    """Whether path records a worktree being added, removed or switched: `.git/worktrees`, `.git/worktrees/<name>`, or its HEAD.

    The rest of a worktree's directory changes on every `git status` run inside it.
    `.git/worktrees` counts because a repo's first `git worktree add` creates it, and a watch
    reaches a new directory's contents only once it has registered that directory. By then
    git has usually written the worktree's directory and HEAD, so neither raises an event.
    """
    parts = path.parts
    for at in range(len(parts) - 1):
        if parts[at] == '.git' and parts[at + 1] == 'worktrees':
            return len(parts) - at <= 3 or parts[at + 3 :] == ('HEAD',)
    return False


def could_be_indexed(path: Path, root: LiveRoot) -> bool:
    """Whether a path under root falls outside SKIP_DIRS and the root's excludes. Asks nothing of the disk."""
    return not is_in_skip_dir(path, root.path) and not is_excluded(path.relative_to(root.path).as_posix(), root.exclude)


def drop_gitignored(root: Path, paths: list[Path]) -> list[Path]:
    """paths without the ones root's gitignore rules exclude. Unchanged when root is not a git repo.

    The same rule the full index follows through `git ls-files --exclude-standard`: a tracked
    file is never ignored, whatever .gitignore says. A root that has itself been removed, as a
    worktree is, passes its deletions through for update_overlay to drop.
    """
    if not root.is_dir():
        return paths
    result = subprocess.run(['git', 'check-ignore', '--stdin'], cwd=root, input='\n'.join(map(str, paths)), capture_output=True, text=True)
    if result.returncode not in (0, 1):
        return paths
    ignored = {Path(line) for line in result.stdout.splitlines()}
    return [path for path in paths if path not in ignored]


@contextmanager
def base_reader() -> Iterator[sqlite3.Connection | None]:
    """The base alone, read-only, or None on a machine that has no index yet."""
    try:
        conn = open_base_readonly()
    except IndexNotFoundError:
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='ignore')


def remove_from_overlay(conn: sqlite3.Connection, db_path: str, root: LiveRoot) -> LiveChange | None:
    """A file gone from disk: drop what the overlay holds for it, and hide any rows the base has."""
    if get_tombstone(conn, db_path):
        return None
    held = clear_overlay_file(conn, db_path)
    in_base = False
    if root.checkout is None:
        with base_reader() as base:
            in_base = base is not None and get_indexed_file(base, db_path) is not None
    if not (held or in_base):
        return None
    now = dt.datetime.now(dt.UTC).isoformat()
    if in_base:
        put_tombstone(conn, db_path, root.label, now)
    mark_overlay_updated(conn, now)
    conn.commit()
    return LiveChange(db_path, root.label, root.branch, 'removed')


def update_overlay(conn: sqlite3.Connection, path: Path, root: LiveRoot) -> LiveChange | None:
    """Bring the overlay's rows for one file in line with the file on disk. Returns what changed, or None.

    The overlay holds a file only while its content differs from what a read gets without it:
    the base's for a main checkout's file, the main checkout's for a worktree's. A file saved
    back to that content leaves the overlay rather than shadowing it with a copy.

    Only chunks whose text is new are embedded. Every other chunk reuses the vector already
    stored for that text — in the overlay, in the base, or for a worktree in the main
    checkout's rows — since an embedding depends only on its text and the model.

    A file held at the same content is left alone unless another extractor version produced
    its references, in which case its rows are rewritten from the stored vectors. A worktree's
    file is still compared with the main checkout's first, since that copy can change to match.
    """
    db_path = compact_path(str(path))
    try:
        if not path.exists():
            return remove_from_overlay(conn, db_path, root)
        if not path.is_file() or not should_index(path):
            return None
        content = read_text(path)
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return remove_from_overlay(conn, db_path, root)

    content_hash = compute_content_hash(content)
    held = get_indexed_file(conn, db_path)
    current = held is not None and held['content_hash'] == content_hash and held['extractor_version'] == EXTRACTOR_VERSION
    if current and root.checkout is None:
        return None

    twin = root.checkout / path.relative_to(root.path) if root.checkout else None
    vector_paths = [db_path] if twin is None else [compact_path(str(twin)), db_path]
    vectors: dict[str, bytes | list[float]] = {}
    with base_reader() as base:
        if twin is None:
            baseline = get_indexed_file(base, db_path) if base else None
            unchanged = baseline is not None and baseline['content_hash'] == content_hash
        else:
            unchanged = twin.is_file() and compute_content_hash(read_text(twin)) == content_hash
        if not (unchanged or current):
            for vector_path in vector_paths:
                vectors |= get_chunk_vectors(base, vector_path) if base else {}
                vectors |= get_chunk_vectors(conn, vector_path)

    if unchanged:
        if not clear_overlay_file(conn, db_path):
            return None
        mark_overlay_updated(conn, dt.datetime.now(dt.UTC).isoformat())
        conn.commit()
        return LiveChange(db_path, root.label, root.branch, 'restored')
    if current:
        return None

    chunks = chunk_file(db_path, content, root.label)
    embedded = 0
    for chunk in chunks:
        if chunk.text not in vectors:
            vectors[chunk.text] = embed_text(chunk.text)
            embedded += 1
    language = detect_language(str(path), content)
    now = dt.datetime.now(dt.UTC).isoformat()

    clear_overlay_file(conn, db_path)
    for chunk in chunks:
        chunk_id = insert_chunk(
            conn,
            {
                'file_path': db_path,
                'repo': root.label,
                'language': language,
                'symbol_name': chunk.symbol_name,
                'symbol_type': chunk.symbol_type,
                'module': chunk.module,
                'text': chunk.text,
            },
        )
        insert_chunk_embedding(conn, chunk_id, vectors[chunk.text])
    refs = extract_references(db_path, content)
    if refs:
        insert_references(conn, [asdict(r) for r in refs])
    insert_overlay_file(
        conn,
        {
            'repo': root.label,
            'file_path': db_path,
            'language': language,
            'mtime': mtime,
            'content_hash': content_hash,
            'chunk_count': len(chunks),
            'indexed_at': now,
            'extractor_version': EXTRACTOR_VERSION,
            'branch': root.branch,
        },
    )
    mark_overlay_updated(conn, now)
    conn.commit()
    return LiveChange(db_path, root.label, root.branch, 'indexed', embedded=embedded, reused=len(chunks) - embedded)


def stale_live_paths(root: LiveRoot) -> list[Path]:
    """Files under root that changed or went while nothing was watching: the backlog a watch starts with.

    Compared by mtime against what a read sees, so an untouched file costs one stat. A
    different mtime only queues the file; update_overlay hashes it and decides. A worktree file
    byte-identical to the main checkout's is never queued, which is almost all of them. A
    tombstoned file that is back on disk is queued, since its mtime can match the base's.

    A file the overlay holds under another extractor version is queued to have its references
    extracted again.

    Nothing on a machine with no base yet. The first full index builds that, and catching up
    against an empty base would embed the whole corpus into the overlay instead.
    """
    files = collect_indexable_files(root.path, root.exclude)
    if not files:
        return []
    prefix = compact_path(str(root.path)).rstrip('/') + '/'
    with base_reader() as base:
        if base is None:
            return []
        known = get_file_states(base, prefix) if root.checkout is None else {}
    with closing(open_overlay_db()) as overlay:
        known |= get_file_states(overlay, prefix)
        buried = get_tombstoned_paths(overlay, prefix)
        outdated = get_paths_extracted_by_another_version(overlay, prefix, EXTRACTOR_VERSION)

    stale = []
    for path in files:
        db_path = compact_path(str(path))
        if db_path in outdated:
            stale.append(path)
            continue
        state = known.get(db_path)
        try:
            if state is not None and state[1] == path.stat().st_mtime:
                continue
            if state is None and root.checkout is not None:
                twin = root.checkout / path.relative_to(root.path)
                if twin.is_file() and filecmp.cmp(path, twin, shallow=False):
                    continue
        except OSError:
            pass
        stale.append(path)
    gone = [Path(expand_path(db_path)) for db_path in known if db_path not in buried and not Path(expand_path(db_path)).exists()]
    revived = [Path(expand_path(db_path)) for db_path in buried if Path(expand_path(db_path)).exists()]
    return stale + gone + revived


def compact_live_overlay(read_here: Collection[str] = ()) -> dict | None:
    """Drop what the overlay holds that the base now matches. None where the index has no overlay.

    read_here names the labels this machine just indexed, as storage.compact_overlay takes them.
    """
    if not overlay_path().exists():
        return None
    with closing(open_overlay_db()) as conn:
        return compact_overlay(conn, read_here)


def warm_model() -> None:
    """Load the embedding model and keep it loaded past ollama's default idle unload."""
    httpx2.post(f'{OLLAMA_HOST}/api/embed', json={'model': OLLAMA_MODEL, 'input': 'warm', 'keep_alive': '10m'}, timeout=30.0)


def drop_worktree(conn: sqlite3.Connection, root: Path) -> int:
    """Remove every overlay row under a worktree that no longer exists. Returns the file count."""
    paths = get_overlay_paths(conn, compact_path(str(root)).rstrip('/') + '/')
    for path in paths:
        clear_overlay_file(conn, path)
    conn.commit()
    return len(paths)


def watch(
    stop: threading.Event,
    on_change: Callable[[LiveChange], None],
    on_note: Callable[[str], None],
    on_problem: Callable[[str], None],
) -> None:
    """Keep the overlay current with every save under every live root, until stop is set.

    The calling thread watches the filesystem and queues each changed path, newest first. One
    worker thread indexes the queue a file at a time. A file whose update raised goes back on
    the queue after a backoff. A save in a main checkout also queues the same file in each of
    its linked worktrees, which are held only while they differ from it.

    A second thread walks every root to queue what changed while nothing was watching, behind
    any live save. It walks them once the watch has registered every directory, at the start
    and after every restart of the watch, because a change made before then raised no event.
    It also compacts the overlay whenever a new base replaces the one it was reconciled with,
    whether this machine built it or a syncer delivered it. Then it walks every root again,
    since the new base may hold another machine's copy of a file. A third thread keeps the
    model loaded, so the first save after a quiet hour does not wait for it.

    A worktree being added, removed or switched lists the roots again. When they differ, a
    removed worktree's rows are dropped, a switched one's relabeled, and the watch restarts
    over the new set.
    """
    queue = WorkQueue()
    current = {'roots': live_roots()}
    catch_up_due = threading.Event()

    def describe(roots: list[LiveRoot]) -> str:
        worktrees = sum(1 for root in roots if root.branch is not None)
        return f'{len(roots) - worktrees} roots and {worktrees} worktrees'

    def drain() -> None:
        attempts: dict[Path, int] = {}
        retries: list[tuple[float, Path]] = []
        with closing(open_overlay_db()) as conn:
            while not stop.is_set():
                while retries and retries[0][0] <= time.monotonic():
                    queue.push_newest([heapq.heappop(retries)[1]])
                path = queue.pop(timeout=0.5)
                roots = current['roots']
                root = root_for(path, roots) if path else None
                if path is None or root is None:
                    continue
                started = time.perf_counter()
                try:
                    change = update_overlay(conn, path, root)
                except Exception as exc:
                    conn.rollback()
                    tries = attempts.get(path, 0) + 1
                    delay = RETRY_FIRST_SECONDS * 2 ** (tries - 1)
                    if delay > RETRY_LAST_SECONDS:
                        attempts.pop(path, None)
                        on_problem(f'{compact_path(str(path))}: {exc}. Left as it is until it changes again.')
                    else:
                        attempts[path] = tries
                        heapq.heappush(retries, (time.monotonic() + delay, path))
                        on_problem(f'{compact_path(str(path))}: {exc}. Trying again in {delay:.0f}s.')
                    continue
                attempts.pop(path, None)
                if change:
                    on_change(replace(change, seconds=time.perf_counter() - started))
                if root.branch is None:
                    relative = path.relative_to(root.path)
                    queue.push_newest([tree.path / relative for tree in roots if tree.checkout == root.path])

    def reconcile() -> None:
        starting = True
        with closing(open_overlay_db()) as conn:
            while not stop.is_set():
                if base_stamp() != get_reconciled_stamp(conn):
                    try:
                        dropped = compact_overlay(conn)
                    except Exception as exc:
                        on_problem(f'reconciling the overlay with the index: {exc}')
                    else:
                        on_note(f'Reconciled with the index: dropped {dropped["files"]} files and {dropped["tombstones"]} deletions')
                        # At the start, the catch-up after registration already walks every root.
                        if not starting:
                            catch_up_due.set()
                starting = False
                if catch_up_due.wait(BASE_POLL_SECONDS):
                    catch_up_due.clear()
                    catch_up(current['roots'])

    def catch_up(roots: list[LiveRoot]) -> None:
        for root in roots:
            if stop.is_set():
                return
            try:
                stale = stale_live_paths(root)
            except Exception as exc:
                on_problem(f'catching up {compact_path(str(root.path))}: {exc}')
                continue
            if stale:
                queue.push_oldest(stale)
                on_note(f'Catching up {len(stale)} file{"" if len(stale) == 1 else "s"} in {compact_path(str(root.path))}')

    def keep_warm() -> None:
        while not stop.is_set():
            try:
                warm_model()
            except Exception as exc:
                on_problem(f'warming {OLLAMA_MODEL}: {exc}')
            stop.wait(KEEP_WARM_SECONDS)

    on_note(f'Watching {describe(current["roots"])}. Overlay: {overlay_path()}')
    # Created before any thread opens it. Two connections creating it at once race to switch
    # it to WAL and to add its columns, and the loser raises.
    open_overlay_db().close()
    worker = threading.Thread(target=drain, name='indy-watch-index', daemon=True)
    for thread in (
        worker,
        threading.Thread(target=keep_warm, daemon=True),
        threading.Thread(target=reconcile, daemon=True),
    ):
        thread.start()

    try:
        while not stop.is_set():
            roots = current['roots']
            registered = False
            for batch in watch_changes([root.path for root in roots], accepts_live_event(roots), stop):
                if not registered:
                    registered = True
                    catch_up_due.set()
                by_root: dict[Path, list[Path]] = {}
                for path in batch:
                    root = root_for(path, roots)
                    if root is not None and not is_worktree_event(path):
                        by_root.setdefault(root.path, []).append(path)
                for root_path, paths in by_root.items():
                    queue.push_newest(drop_gitignored(root_path, paths))
                if any(is_worktree_event(path) for path in batch):
                    fresh = live_roots()
                    if fresh != roots:
                        reconcile_worktrees(roots, fresh, on_note)
                        current['roots'] = fresh
                        on_note(f'Watching {describe(fresh)}')
                        break
    finally:
        stop.set()
        worker.join()


def accepts_live_event(roots: list[LiveRoot]) -> Callable[[Path], bool]:
    """The filter a watch over roots applies to each raw event, before anything reads the disk."""

    def accepts(path: Path) -> bool:
        root = root_for(path, roots)
        return root is not None and (is_worktree_event(path) or could_be_indexed(path, root))

    return accepts


def reconcile_worktrees(before: list[LiveRoot], after: list[LiveRoot], on_note: Callable[[str], None]) -> None:
    """Bring the overlay's worktree rows in line with a new list of roots. The catch-up after the restart covers what it added."""
    was = {root.path: root for root in before if root.branch is not None}
    now = {root.path: root for root in after if root.branch is not None}
    with closing(open_overlay_db()) as conn:
        for path in was.keys() - now.keys():
            on_note(f'Worktree {compact_path(str(path))} is gone; dropped {drop_worktree(conn, path)} files')
        for path in was.keys() & now.keys():
            branch = now[path].branch
            if branch is not None and branch != was[path].branch:
                set_overlay_branch(conn, compact_path(str(path)).rstrip('/') + '/', branch)
                conn.commit()


def expand_result_paths(results: list[dict], key: str = 'file_path') -> list[dict]:
    """Expand ~-prefixed paths in a list of result dicts back to absolute paths."""
    for r in results:
        if key in r:
            r[key] = expand_path(r[key])
    return results


def located(results: list[dict], key: str = 'file_path') -> list[dict]:
    """Results with absolute paths, each carrying the branch of the worktree it came from, or None.

    Only an overlay row can name a branch, so a read with no overlay sets every one to None
    rather than leaving the key out.
    """
    for r in expand_result_paths(results, key):
        r.setdefault('branch', None)
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
        return located(search_chunks(conn, embedding, repo=repo, language=language, limit=limit, repos=repos))
    finally:
        conn.close()


def search_symbol(name: str, repo: str | None = None) -> list[dict]:
    conn = get_read_db()
    try:
        return located(get_chunks_by_symbol(conn, name, repo=repo))
    finally:
        conn.close()


def summarize_freshness(last_scans: dict[str, str], scope: set[str] | None = None) -> dict | None:
    """Bound the age of everything a read can reach: the oldest scan in scope, and the newest.

    The oldest is the number that answers "can I trust a miss", and reporting only the newest
    hides it. Indexing one repo makes the newest scan minutes old while every other label
    stays where it was, so a read quoting the newest calls itself current at exactly the
    moment it is most likely to be answering out of date. The oldest is the bound every
    result in scope actually satisfies.

    None when nothing in scope has been scanned, which is the whole of a fresh install.
    """
    in_scope = {repo: scanned for repo, scanned in last_scans.items() if scope is None or repo in scope}
    if not in_scope:
        return None

    now = dt.datetime.now(dt.UTC)

    def describe(repo: str) -> dict:
        return {
            'repo': repo,
            'scanned_at': in_scope[repo],
            'age_seconds': (now - dt.datetime.fromisoformat(in_scope[repo])).total_seconds(),
        }

    return {
        'repos': len(in_scope),
        'oldest': describe(min(in_scope, key=lambda repo: in_scope[repo])),
        'newest': describe(max(in_scope, key=lambda repo: in_scope[repo])),
    }


def index_freshness(repo: str | None = None, owned: bool | None = None) -> dict | None:
    """How far behind the index is over the repos one read could match.

    Scoped by the read's own filters, so the answer describes what was searched rather than
    the whole database. A repo the index has never held is outside every scope: this
    measures the index, and a target that was never indexed is a gap in coverage rather than
    an age.
    """
    scope = {repo} if repo else resolve_ownership_filter(owned)
    conn = get_read_db()
    try:
        return read_freshness(conn, scope)
    finally:
        conn.close()


def read_freshness(conn: sqlite3.Connection, scope: set[str] | None = None) -> dict | None:
    """The base's horizon over scope, and what the overlay holds beyond it, or None for an overlay this machine lacks.

    The overlay's count says how many files a read sees newer than the scan, and its age says
    whether `indy watch` is still writing.
    """
    freshness = summarize_freshness(get_last_scans(conn), scope)
    if freshness is None:
        return None
    live = get_overlay_summary(conn, scope)
    if live is not None and live['updated_at']:
        live['age_seconds'] = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(live['updated_at'])).total_seconds()
    freshness['overlay'] = live
    return freshness


def get_status() -> dict:
    conn = get_read_db()
    try:
        stats = get_index_stats(conn)
        stats['freshness'] = read_freshness(conn)
        stats['recent_runs'] = get_recent_runs(conn, limit=5)
        stats['scip'] = get_scip_states(conn)
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
        'files_pruned': sum(r['files_pruned'] for r in results),
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


def forget_repo(repo: str, on_stage: Callable[[str], None] | None = None) -> dict:
    """Delete everything indexed under one repo label. Returns what was deleted, per table.

    For a label the registry no longer produces: a target that was renamed, or absorbed
    into a wider one. Re-indexing cannot clear it, because a file whose content_hash is
    unchanged is skipped and keeps the label it was first indexed under.

    Recoverable by construction — the filesystem is the source of truth and the next
    index rebuilds whatever is still a target, which is why this asks for no
    confirmation, as `clear_errors` does not either.
    """
    with index_session(on_stage=on_stage) as session:
        removed = session.forget_repo(repo)
    if overlay_path().exists():
        with closing(open_overlay_db()) as overlay:
            removed['files'] += delete_overlay_label(overlay, repo)
    return removed


def indexed_repo_names() -> list[str]:
    """Every label the index currently holds, whether or not it is still a target."""
    conn = get_read_db()
    try:
        return sorted({row[0] for row in conn.execute('SELECT DISTINCT repo FROM indexed_file')})
    finally:
        conn.close()


def is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = parts[-1] if parts else ''
    return bool({'tests', 'test'} & set(parts)) or name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py'


def split_symbol(symbol_name: str) -> tuple[str, str, str]:
    """`Owner.member[...]` as owner, member and subscript, any of which may be empty.

    The subscript is what a dispatch-table caller prints as, `STEPS['wslconfig']`, so a name
    pasted back from deps output resolves.
    """
    base, bracket, rest = symbol_name.partition('[')
    owner, _, member = base.rpartition('.')
    return owner, member, bracket + rest


def symbol_owner(symbol: str) -> str | None:
    """What a SCIP symbol belongs to, as `deps` prints a target: its class, or its module's last component."""
    named = name_symbol(symbol)
    if named is None:
        return None
    return named.name.rpartition('.')[0] or named.module.rpartition('.')[2] or None


def scip_caller_label(use: dict, overridden: set[str], rules: dict[tuple[str, str | None], str]) -> str:
    """How a caller scip-python found reaches the target.

    `override` where it calls a method the target overrides. Otherwise the label of the
    syntactic rule reaching the same caller, if one does. Otherwise `inferred` for a name read
    off a receiver no rule types, and `call` or `reference` for a bare name, which reaches its
    symbol by import or scope rather than by any type.
    """
    if use['symbol'] in overridden:
        return 'override'
    rule = rules.get((use['file_path'], use['source_symbol']))
    if rule:
        return rule
    return 'inferred' if use['on_receiver'] else use['ref_type']


def find_callers(conn: sqlite3.Connection, symbol_name: str, repo: str | None = None) -> tuple[list[dict], list[str]]:
    """Callers of a bare name or of `Owner.member`, with candidates and then tests last, and the repos scip-python resolved them in.

    Where scip-python defines the symbol, the repos it ran over take their callers from it,
    labeled by scip_caller_label. A syntactic candidate there stands only where scip-python
    resolved nothing of that name in the same caller. Every other file, including any the
    overlay holds, keeps the syntactic rules.

    The sort is stable, so within each group the rows keep the order they were found in.
    """
    owner, member, _ = split_symbol(symbol_name)
    syntactic = get_member_callers(conn, owner, member, repo=repo) if owner else get_symbol_callers(conn, member, repo=repo)
    for row in syntactic:
        row['line'] = None
    targets = get_scip_targets(conn, owner, member)
    rows = syntactic
    linked: dict[str, str] = {}
    if targets:
        symbols = {target['symbol'] for target in targets}
        overridden = get_scip_overridden(conn, symbols)
        linked = get_scip_documents(conn, {target['repo'] for target in targets})
        rules = {(row['source_file'], row['source_symbol']): row['via'] for row in syntactic if row['via'] != 'candidate'}
        resolved_sites = get_scip_call_sites(conn, member)
        rows = [
            {
                'source_file': use['file_path'],
                'source_symbol': use['source_symbol'],
                'target_symbol': member,
                'target_module': symbol_owner(use['symbol']),
                'ref_type': use['ref_type'],
                'line': use['line'],
                'via': scip_caller_label(use, overridden, rules),
            }
            for use in get_scip_references(conn, symbols | overridden, repo)
        ]
        rows += [
            row
            for row in syntactic
            if row['source_file'] not in linked
            or (row['via'] == 'candidate' and (row['source_file'], row['source_symbol']) not in resolved_sites)
        ]
    ordered = sorted(rows, key=lambda row: (row['via'] == 'candidate', is_test_path(row['source_file'])))
    return ordered, sorted(set(linked.values()))


def find_callees(conn: sqlite3.Connection, symbol_name: str, repo: str | None = None) -> list[dict]:
    """Calls made inside a symbol. `module.function` is matched as `function` in files named `module`.

    A method's source symbol is `Class.method` already, so an owner that is an indexed class is
    matched whole. In a file scip-python linked, a call it resolved to one symbol is printed
    under that symbol's owner rather than the receiver's name.
    """
    owner, member, subscript = split_symbol(symbol_name)
    stem = owner.rpartition('.')[2]
    if stem and not is_indexed_class(conn, stem):
        rows = get_symbol_callees(conn, member + subscript, repo=repo, file_stem=stem)
    else:
        rows = get_symbol_callees(conn, symbol_name, repo=repo)
    if not has_scip_tables(conn):
        return rows
    for row in rows:
        resolved = get_scip_call_targets(conn, row['source_file'], row['source_symbol'], row['target_symbol'])
        owners = {symbol_owner(symbol) for symbol in resolved}
        if len(owners) == 1 and None not in owners:
            row['target_module'] = owners.pop()
    return rows


def get_dependencies(symbol_name: str, repo: str | None = None, direction: str = 'both') -> dict:
    """Return callers and/or callees of a symbol from the reference table.

    direction='callers' → what calls symbol_name
    direction='callees' → what symbol_name calls
    direction='both'    → both

    `resolved_by_scip` names the repos whose callers scip-python resolved, where a missing
    caller means the type checker found none rather than that no rule matched.
    """
    conn = get_read_db()
    try:
        result: dict = {}
        if direction in ('callers', 'both'):
            callers, result['resolved_by_scip'] = find_callers(conn, symbol_name, repo=repo)
            result['callers'] = located(callers, key='source_file')
        if direction in ('callees', 'both'):
            result['callees'] = located(find_callees(conn, symbol_name, repo=repo), key='source_file')
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
        describe_setting('repos_registry', str(REPOS_REGISTRY), 'INDY_REPOS_REGISTRY', 'repos_registry'),
        describe_setting('exemplar_registry', str(EXEMPLAR_REGISTRY), 'INDY_EXEMPLAR_REGISTRY', 'exemplar_registry'),
        describe_setting('ollama_host', OLLAMA_HOST, 'OLLAMA_HOST', 'ollama_host'),
        describe_setting('ollama_model', OLLAMA_MODEL, 'OLLAMA_MODEL', 'ollama_model'),
        {'name': 'overlay', 'value': str(overlay_path()), 'source': 'derived from data_dir', 'exists': overlay_path().exists()},
    ]
    return {
        'config_file': str(CONFIG_PATH),
        'config_file_exists': CONFIG_PATH.exists(),
        'settings': settings,
        'extra_paths': [
            {'name': entry['name'], 'path': entry['path'], 'exists': Path(entry['path']).expanduser().exists()} for entry in EXTRA_PATHS_RAW
        ],
    }
