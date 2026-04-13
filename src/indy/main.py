import time
from collections.abc import Callable
from pathlib import Path

import typer
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
from indy.charts import vertical_bars
from indy.repos import load_active_repos
from indy.repos import load_extra_paths

indy_app = typer.Typer(name='indy', no_args_is_help=True, help='Semantic search index for local codebases, docs, and notes.')
console = Console(highlight=False)


@indy_app.command('status')
def status():
    """Show index health: totals, recent runs, and error count."""
    stats = service.get_status()
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
    path: str = typer.Argument(None, help='Path to index. Omit to index all repos from repos.json.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Repo name from repos.json.'),
    all_repos: bool = typer.Option(False, '--all', help='Index all active repos from repos.json.'),
):
    """Index a path or repo into the semantic search store."""
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
        with progress:
            task_id = progress.add_task(repo, total=None, current_file='')
            try:
                result = service.index_repo(repo, on_progress=make_progress_callback(task_id))
            except ValueError as exc:
                console.print(f'[red]{exc}[/red]')
                raise typer.Exit(1) from exc
        print_index_result(result)

    else:
        all_targets = load_active_repos() + load_extra_paths()
        results = []
        with progress:
            for target in all_targets:
                task_id = progress.add_task(target['name'], total=None, current_file='')
                result = service.index_path(target['path'], target['name'], on_progress=make_progress_callback(task_id))
                results.append(result)
                progress.remove_task(task_id)
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
):
    """Semantic search across indexed code and notes."""
    results = service.search(query, repo=repo, language=language, limit=limit)

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


@indy_app.command('stats')
def stats(
    repo: str = typer.Argument(None, help='Show scan history for a specific repo.'),
):
    """Show index size per repo with charts, or scan history for a specific repo."""
    if repo:
        history = service.repo_scan_history(repo)
        if not history:
            console.print(f'No scan history for {repo!r}.')
            raise typer.Exit(1)

        labels = [h['started_at'][5:10] for h in history]
        files_updated = [float(h['files_updated'] or 0) for h in history]
        chunks_added = [float(h['chunks_added'] or 0) for h in history]

        vertical_bars(
            f'{repo} — files updated per scan',
            labels,
            files_updated,
            color='green',
            height=10,
            console=console,
        )
        vertical_bars(
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
def repos():
    """List all indexed repos with file and chunk counts."""
    repo_list = service.list_repos()
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


@indy_app.command('mcp')
def mcp():
    """Show MCP server setup command."""
    typer.echo('Add to ~/.claude/settings.json:')
    typer.echo('  claude mcp add indy -- python -m indy.mcp')


if __name__ == '__main__':
    indy_app()
