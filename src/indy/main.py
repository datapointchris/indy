import time
from collections import Counter
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

indy_app = typer.Typer(name='indy', no_args_is_help=True, help='Semantic search index for local codebases, docs, and notes.')
console = Console(highlight=False)


@indy_app.command('status')
def status():
    """Show index health: repos indexed, file counts, last run, errors."""
    stats = service.get_status()
    repo_label = f'(across {stats["repo_count"]} repos)' if stats['repo_count'] else ''
    console.print(f'[bold]Files indexed:[/bold] {stats["total_files"]}  {repo_label}')
    console.print(f'[bold]Total chunks:[/bold]  {stats["total_chunks"]}')

    last = stats.get('last_run')
    if last:
        console.print()
        console.print(f'[bold]Last run:[/bold] {last["repo"]} @ {last["started_at"]}')
        if last['finished_at']:
            console.print(f'  scanned {last["files_scanned"]}, updated {last["files_updated"]}, +{last["chunks_added"]} chunks')
        else:
            console.print('  [yellow]interrupted (did not finish)[/yellow]')
        if last.get('error'):
            console.print(f'  [red]run error: {last["error"]}[/red]')

    errors = stats.get('error_file_details', [])
    if errors:
        console.print()
        console.print(f'[bold red]Error files: {len(errors)}[/bold red]')
        # Group by repo and error message for a compact summary
        by_repo: dict[str, Counter[str]] = {}
        for ef in errors:
            msg = ef['status'].removeprefix('error: ')
            # Normalize messages: keep only the first line / first 80 chars
            msg = msg.split('\n')[0]
            if len(msg) > 80:
                msg = msg[:77] + '...'
            by_repo.setdefault(ef['repo'], Counter())[msg] += 1
        for repo, msg_counts in sorted(by_repo.items()):
            console.print(f'  {repo}')
            for msg, count in msg_counts.most_common():
                console.print(f'    [red]✗[/red] {count} file{"s" if count > 1 else ""}: {msg}')


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
        with progress:
            task_id = progress.add_task('all repos', total=None, current_file='')
            results = service.index_all(on_progress=make_progress_callback(task_id))
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
