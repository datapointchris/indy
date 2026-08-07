import importlib.metadata
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.typercmd import run_update
from rich.console import Console
from rich.progress import BarColumn
from rich.progress import MofNCompleteColumn
from rich.progress import Progress
from rich.progress import SpinnerColumn
from rich.progress import TaskID
from rich.progress import TextColumn
from rich.table import Table

import indy.service as service
from indy.charts import horizontal_bars
from indy.charts import streamline
from indy.repos import all_index_targets
from indy.repos import get_target_by_name

indy_app = typer.Typer(name='indy', no_args_is_help=True, help='Semantic search index for local codebases, docs, and notes.')
console = Console(highlight=False)


# Shared by the `update` command and the daily check below, so the notice cannot
# name a release the update command would not install.
UPDATE_CONFIG = Config(tool='indy', owner='datapointchris')


def _version_callback(asked: bool) -> None:
    """`indy --version`, in the one line every CLI here answers it with."""
    if not asked:
        return
    try:
        version = importlib.metadata.version('indy')
    except importlib.metadata.PackageNotFoundError:
        version = 'unknown'
    commit = get_installed_commit_hash()
    console.print(f'indy {version}{f" @ {commit[:8]}" if commit else ""}')
    raise typer.Exit()


@indy_app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option('--version', callback=_version_callback, is_eager=True, help='Show the installed version and exit.'),
    ] = None,
) -> None:
    """Root callback, hosting the daily update check and app-level options.

    Never raises and never prints an error; the notice is deferred to exit so it
    lands after the command's own output. `indy update` is the only place an
    update failure is reported, and is skipped here because it is about to do
    the thing the notice would suggest.
    """
    if ctx.invoked_subcommand != 'update':
        notify(UPDATE_CONFIG)


def print_json(data: object) -> None:
    """Emit JSON to stdout, bypassing Rich markup — the scripting/agent-facing output shared by every --json flag."""
    print(json.dumps(data))


@indy_app.command('status')
def status(
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Show index health: totals, recent runs, and error count."""
    stats = service.get_status()
    if as_json:
        print_json(stats)
        return
    repo_label = f'(across {stats["repo_count"]} repos)' if stats['repo_count'] else ''
    console.print(f'[bold]Files indexed:[/bold] {stats["total_files"]}  {repo_label}')
    console.print(f'[bold]Total chunks:[/bold]  {stats["total_chunks"]}')

    recent = stats.get('recent_runs', [])
    if recent:
        console.print()
        console.print('[bold]Recent:[/bold]')
        for run in recent:
            updated = f'updated {run["files_updated"]}, +{run["chunks_added"]} chunks' if run['files_updated'] else 'no changes'
            console.print(f'  {run["repo"]:<25s} scanned {run["files_scanned"]}, {updated}')

    error_count = stats.get('error_files', 0)
    if error_count:
        console.print()
        console.print(f'[bold red]Error files: {error_count}[/bold red]  (run [bold]indy errors[/bold] for details)')


@indy_app.command('index')
def index(
    repo: str = typer.Argument(None, help='Repo name from repos.json. Omit to index all.'),
    path: str = typer.Option(None, '--path', '-p', help='Arbitrary path to index.'),
):
    """Index a repo or path into the semantic search store."""
    if repo and path:
        console.print('[red]Cannot specify both repo name and --path.[/red]')
        raise typer.Exit(1)

    t0 = time.perf_counter()

    progress = Progress(
        SpinnerColumn(),
        TextColumn('[bold]{task.description}[/bold]'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('{task.fields[current_file]}'),
        console=console,
    )

    def make_progress_callback(task_id: TaskID) -> Callable[[int, int, str], None]:
        def on_progress(files_scanned: int, total_files: int, current_file: str) -> None:
            progress.update(task_id, total=total_files, completed=files_scanned, current_file=current_file)

        return on_progress

    if path:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            console.print(f'[red]Path not found: {root}[/red]')
            raise typer.Exit(1)
        repo_name = root.name
        with progress:
            task_id = progress.add_task(repo_name, total=None, current_file='')
            result = service.index_path(root, repo_name, on_progress=make_progress_callback(task_id))
        print_index_result(result)

    elif repo:
        if get_target_by_name(repo) is None:
            console.print(f'[red]Repo {repo!r} not found. Exemplar clones are named owner/repo.[/red]')
            raise typer.Exit(1)
        with progress:
            task_id = progress.add_task(repo, total=None, current_file='')
            result = service.index_repo(repo, on_progress=make_progress_callback(task_id))
        print_index_result(result)

    else:
        results = []
        targets = all_index_targets()
        with progress:
            # Per-target tasks are removed as they finish, so without a task spanning the
            # whole list the bar restarts silently and a long run reads as no progress.
            overall = progress.add_task('all targets', total=len(targets), current_file='')
            for target in targets:
                task_id = progress.add_task(target.name, total=None, current_file='')
                result = service.index_path(target.path, target.name, on_progress=make_progress_callback(task_id), exclude=target.exclude)
                results.append(result)
                progress.remove_task(task_id)
                progress.advance(overall)
        for result in results:
            print_index_result(result)
        total_updated = sum(r['files_updated'] for r in results)
        total_chunks = sum(r['chunks_added'] for r in results)
        console.print(f'\n[bold]Done.[/bold] {len(results)} repos, {total_updated} files updated, {total_chunks} chunks added.')

    elapsed = time.perf_counter() - t0
    console.print(f'Completed in {format_elapsed(elapsed)}')


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f'{minutes}m {secs:.1f}s'


def print_index_result(result: dict) -> None:
    status_str = f'[red]error: {result["error"]}[/red]' if result['error'] else '[green]ok[/green]'
    console.print(
        f'  {result["repo"]}: scanned {result["files_scanned"]}, '
        f'updated {result["files_updated"]}, '
        f'+{result["chunks_added"]} chunks — {status_str}'
    )


@indy_app.command('errors')
def errors():
    """Show all indexing errors, grouped by repo with per-file details."""
    stats = service.get_status()
    error_details = stats.get('error_file_details', [])

    if not error_details:
        console.print('No errors.')
        return

    by_repo: dict[str, list[dict]] = {}
    for ef in error_details:
        by_repo.setdefault(ef['repo'], []).append(ef)

    for repo in sorted(by_repo):
        files = sorted(by_repo[repo], key=lambda f: f['file_path'])
        console.print(f'\n[bold]{repo}[/bold] ({len(files)} error{"s" if len(files) != 1 else ""})')
        for ef in files:
            msg = ef['status'].removeprefix('error: ').split('\n')[0]
            console.print(f'  [red]✗[/red] {ef["file_path"]}')
            console.print(f'    {msg}')

    console.print(f'\n{len(error_details)} total — run [bold]indy errors-clear[/bold] to remove')


@indy_app.command('errors-clear')
def errors_clear():
    """Remove all error records from the index, so they are re-attempted on next index run."""
    cleared = service.clear_errors()
    if cleared:
        console.print(f'Cleared {cleared} error record{"s" if cleared != 1 else ""}.')
    else:
        console.print('No error records to clear.')


@indy_app.command('search')
def search(
    query: str = typer.Argument(..., help='Natural language search query.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit search to this repo.'),
    language: str = typer.Option(None, '--language', '-l', help='Limit to file language (python, go, markdown…).'),
    limit: int = typer.Option(10, '--limit', '-n', help='Number of results to return.'),
    owned: bool = typer.Option(False, '--owned', help='Only search your own repos.'),
    reference: bool = typer.Option(False, '--reference', help='Only search reference repos.'),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Semantic search across indexed code and notes."""
    if owned and reference:
        console.print('[red]Cannot use --owned and --reference together.[/red]')
        raise typer.Exit(1)
    ownership = True if owned else (False if reference else None)
    results = service.search(query, repo=repo, language=language, limit=limit, owned=ownership)

    if as_json:
        for r in results:
            r['score'] = round(1 - r.pop('distance'), 4)
        print_json({'results': results})
        return

    if not results:
        console.print('No results.')
        return

    for i, r in enumerate(results, 1):
        score = 1 - r['distance']  # convert distance to similarity
        symbol = f' · {r["symbol_name"]}' if r.get('symbol_name') else ''
        console.print(f'\n[bold cyan]{i}.[/bold cyan] {r["file_path"]}{symbol}  (score: {score:.2f})')
        snippet = r['text'][:300].replace('\n', '\n   ')
        console.print(f'   {snippet}')
        if len(r['text']) > 300:
            console.print('   …')


@indy_app.command('symbol')
def symbol(
    name: str = typer.Argument(..., help='Exact function or class name to look up.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit to this repo.'),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Exact symbol lookup by function or class name — precise alternative to semantic search."""
    results = service.search_symbol(name, repo=repo)
    if as_json:
        print_json({'results': results})
        return

    if not results:
        console.print(f'No symbol named {name!r} found.')
        return

    for i, r in enumerate(results, 1):
        kind = f'{r["symbol_type"]} ' if r.get('symbol_type') else ''
        console.print(f'\n[bold cyan]{i}.[/bold cyan] {kind}[bold]{r["symbol_name"]}[/bold] — {r["file_path"]}')
        snippet = r['text'][:300].replace('\n', '\n   ')
        console.print(f'   {snippet}')
        if len(r['text']) > 300:
            console.print('   …')


@indy_app.command('deps')
def deps(
    symbol_name: str = typer.Argument(..., help='Symbol to trace through the reference graph.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit to this repo.'),
    direction: str = typer.Option('both', '--direction', '-d', help="'callers', 'callees', or 'both'."),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Find what calls a symbol or what it calls, using the Python reference graph."""
    if direction not in ('callers', 'callees', 'both'):
        console.print("[red]--direction must be 'callers', 'callees', or 'both'.[/red]")
        raise typer.Exit(1)

    result = service.get_dependencies(symbol_name, repo=repo, direction=direction)
    if as_json:
        print_json(result)
        return

    callers = result.get('callers', [])
    callees = result.get('callees', [])
    if not callers and not callees:
        console.print(f'No references found for {symbol_name!r}. (Reference graph is Python-only.)')
        return

    if 'callers' in result:
        console.print(f'\n[bold]Callers of {symbol_name}[/bold] ({len(callers)})')
        if not callers:
            console.print('  (none)')
        for c in callers:
            console.print(f'  [cyan]{c["source_symbol"] or "?"}[/cyan] — {c["source_file"]}')

    if 'callees' in result:
        console.print(f'\n[bold]Callees of {symbol_name}[/bold] ({len(callees)})')
        if not callees:
            console.print('  (none)')
        for c in callees:
            module_prefix = f'{c["target_module"]}.' if c.get('target_module') else ''
            console.print(f'  [cyan]{module_prefix}{c["target_symbol"]}[/cyan] — {c["source_file"]}')


@indy_app.command('stats')
def stats(
    repo: str = typer.Argument(None, help='Show scan history for a specific repo.'),
):
    """Show index size per repo with charts, or scan history for a specific repo."""
    if repo:
        history = service.repo_scan_history(repo)
        if not history:
            if get_target_by_name(repo) is None:
                console.print(f'[red]Repo {repo!r} not found. Exemplar clones are named owner/repo.[/red]')
            else:
                console.print(f'No scan history for {repo!r}. Run [bold]indy index {repo}[/bold] first.')
            raise typer.Exit(1)

        labels = [h['started_at'][5:10] for h in history]
        files_updated = [float(h['files_updated'] or 0) for h in history]
        chunks_added = [float(h['chunks_added'] or 0) for h in history]

        streamline(
            f'{repo} — files updated per scan',
            labels,
            files_updated,
            color='green',
            height=10,
            console=console,
        )
        streamline(
            f'{repo} — chunks added per scan',
            labels,
            chunks_added,
            color='cyan',
            height=10,
            console=console,
        )
        console.print()
        return

    repo_list = service.list_repos()
    if not repo_list:
        console.print('No repos indexed yet.')
        return

    by_files = sorted(repo_list, key=lambda r: r['file_count'], reverse=True)
    horizontal_bars(
        'Files per repo',
        [r['repo'] for r in by_files],
        [float(r['file_count']) for r in by_files],
        color='green',
        console=console,
    )

    by_chunks = sorted(repo_list, key=lambda r: r['chunk_count'], reverse=True)
    horizontal_bars(
        'Chunks per repo',
        [r['repo'] for r in by_chunks],
        [float(r['chunk_count']) for r in by_chunks],
        color='cyan',
        console=console,
    )

    by_date = sorted(repo_list, key=lambda r: r['last_indexed'], reverse=True)
    console.print('\n  [bold]Last indexed[/bold]')
    for r in by_date:
        console.print(f'    {r["repo"]:<25s} {r["last_indexed"][:19]}')

    console.print()


@indy_app.command('repos')
def repos(
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """List all indexed repos with file and chunk counts."""
    repo_list = service.list_repos()
    if as_json:
        print_json({'repos': repo_list})
        return
    if not repo_list:
        console.print('No repos indexed yet. Run [bold]indy index[/bold] to start.')
        return

    table = Table(show_header=True)
    table.add_column('Repo')
    table.add_column('Files', justify='right')
    table.add_column('Chunks', justify='right')
    table.add_column('Last indexed')
    for r in repo_list:
        table.add_row(r['repo'], str(r['file_count']), str(r['chunk_count']), r['last_indexed'])
    console.print(table)


@indy_app.command('config')
def config(
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Show resolved settings and which layer supplied each one."""
    described = service.describe_config()
    if as_json:
        print_json(described)
        return

    console.print(f'Config file: {described["config_file"]}{"" if described["config_file_exists"] else "  (not present)"}')

    table = Table(show_header=True)
    table.add_column('Setting')
    table.add_column('Value')
    table.add_column('From')
    for setting in described['settings']:
        missing = '' if setting['exists'] else '  (missing)'
        table.add_row(setting['name'], f'{setting["value"]}{missing}', setting['source'])
    console.print(table)

    if described['extra_paths']:
        extras = Table(show_header=True, title='extra_paths')
        extras.add_column('Name')
        extras.add_column('Path')
        for entry in described['extra_paths']:
            extras.add_row(entry['name'], f'{entry["path"]}{"" if entry["exists"] else "  (missing)"}')
        console.print(extras)


@indy_app.command('update')
def update(
    check_only: bool = typer.Option(False, '--check', help='Report whether an update is available without installing it'),
) -> None:
    """Update indy to the latest published release."""
    run_update(UPDATE_CONFIG, check_only=check_only)


def get_installed_commit_hash() -> str | None:
    """Read the git commit hash from indy's installed dist-info."""
    try:
        dist = importlib.metadata.distribution('indy')
        direct_url_text = dist.read_text('direct_url.json')
        if direct_url_text:
            return json.loads(direct_url_text).get('vcs_info', {}).get('commit_id')
    except Exception:
        return None
    return None


if __name__ == '__main__':
    indy_app()
