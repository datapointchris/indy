import difflib
import importlib.metadata
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Annotated
from typing import Literal

import typer
from pyselfupdate import Config
from pyselfupdate import notify
from pyselfupdate.typercmd import run_update
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn
from rich.progress import Progress
from rich.progress import SpinnerColumn
from rich.progress import TaskID
from rich.progress import TextColumn
from rich.table import Table

import indy.config as config
import indy.service as service
from indy.charts import horizontal_bars
from indy.charts import streamline
from indy.repos import IndexTarget
from indy.repos import all_index_targets
from indy.repos import get_target_by_name
from indy.storage import IndexNotFoundError


def examples(*rows: tuple[str, str]) -> str:
    """A help screen's examples block: each command beside the situation it answers.

    Written as a markdown list because both renderers between here and the terminal join
    lines. Typer folds an epilog's single line breaks into spaces and keeps a blank line as
    one break, and markdown then reflows consecutive plain lines into one paragraph. A list
    item is the shape that comes out of both as a line of its own. Each row is kept short
    enough to stay on that one line in an 80-column terminal.
    """
    return '\n\n'.join(['Examples:', *(f'- {command} — {purpose}' for command, purpose in rows)])


# Markdown, so a docstring wrapped at the source's width reflows to the terminal's. Under the
# default rich mode, every line break after a command's first paragraph is printed as written.
indy_app = typer.Typer(
    name='indy',
    no_args_is_help=True,
    rich_markup_mode='markdown',
    help=(
        'Semantic search index for local codebases, docs, and notes.\n\n'
        'Start with `indy index` to build the index, then query it. '
        '`indy status` says how old it is and which files failed, '
        'and `indy watch` keeps it current as you save.\n\n'
        'Reach for `search` when you can describe what the code does but not name it, since it ranks by meaning. '
        'Reach for `symbol` or `deps` when you know the exact name, since they match it exactly '
        'and show where it is defined or what calls it.'
    ),
    epilog=examples(
        ('indy search "retry with backoff"', 'find code by what it does, not its name'),
        ('indy symbol index_session', 'jump to a function or class you can name'),
        ('indy deps index_session -d callers', 'what calls it, before you change it'),
        ('indy index', 'bring every registered repo up to date'),
        ('indy status', 'check how old the index is and whether files failed'),
    ),
)
console = Console(highlight=False)
err_console = Console(highlight=False, stderr=True)


# Shared by the `update` command and the daily check below, so the notice cannot
# name a release the update command would not install.
UPDATE_CONFIG = Config(tool='indy', owner='datapointchris')


def version_callback(asked: bool) -> None:
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
def root(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option('--version', callback=version_callback, is_eager=True, help='Show the installed version and exit.'),
    ] = None,
    no_input: Annotated[
        bool,
        typer.Option('--no-input', help='Never prompt. indy has no prompts, so this is always the case.'),
    ] = False,
) -> None:
    """Root callback, hosting the daily update check and app-level options.

    Never raises and never prints an error; the notice is deferred to exit so it
    lands after the command's own output. `indy update` is the only place an
    update failure is reported, and is skipped here because it is about to do
    the thing the notice would suggest.

    `--no-input` is accepted and has no effect. indy never prompts, but a caller
    that passes the flag to every tool it runs must not get a usage error here.
    """
    if ctx.invoked_subcommand != 'update':
        notify(UPDATE_CONFIG)


def print_json(data: object) -> None:
    """Emit JSON to stdout, bypassing Rich markup — the scripting/agent-facing output shared by every --json flag."""
    print(json.dumps(data))


@indy_app.command(
    'status',
    epilog=examples(
        ('indy status', 'how much is indexed, how old it is, and what failed'),
        ('indy status --json', 'the same totals, for a script'),
    ),
)
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
    if stats['freshness']:
        console.print(f'[dim]{describe_freshness(stats["freshness"])}[/dim]')

    recent = stats.get('recent_runs', [])
    if recent:
        console.print()
        console.print('[bold]Recent:[/bold]')
        for run in recent:
            updated = f'updated {run["files_updated"]}, +{run["chunks_added"]} chunks' if run['files_updated'] else 'no changes'
            console.print(f'  {run["repo"]:<25s} scanned {run["files_scanned"]}, {updated}')

    scip = [state for state in stats.get('scip', []) if state['reason'] != 'no Python files']
    if scip:
        resolved = sum(state['status'] == 'ok' for state in scip)
        console.print()
        console.print(f'[bold]scip-python:[/bold] {resolved} of {len(scip)} Python repos resolved')
        for state in scip:
            if state['status'] != 'ok':
                console.print(f'  {state["repo"]:<25s} {describe_scip(state)}')

    error_count = stats.get('error_files', 0)
    if error_count:
        console.print()
        console.print(f'[bold red]Error files: {error_count}[/bold red]  (run [bold]indy errors[/bold] for details)')


def complete_target_name(incomplete: str) -> list[tuple[str, str]]:
    """Shell completion for the repo argument: every name `index` will accept.

    Matches the trailing segment as well as the whole name, because exemplar clones are
    qualified `owner/name` — typing `fastapi` should offer `tiangolo/fastapi` rather than
    nothing, since the owner is the part nobody remembers.
    """
    return [
        (target.name, target.kind)
        for target in all_index_targets()
        if target.name.startswith(incomplete) or target.name.rpartition('/')[2].startswith(incomplete)
    ]


@indy_app.command(
    'index',
    epilog=examples(
        ('indy index', 'bring every registered repo up to date'),
        ('indy index indy', 'refresh one repo after a burst of changes to it'),
        ('indy index --path ~/src/vendored-lib', 'index a directory no registry lists'),
    ),
)
def index(
    repo: str = typer.Argument(None, help='Repo to index. Omit to index everything.', autocompletion=complete_target_name),
    path: str = typer.Option(None, '--path', '-p', help='Index a directory that is not a registered repo.'),
):
    """Index a repo or path into the semantic search store.

    Exemplar clones are named owner/name; everything else is a bare name. Press TAB to
    complete either.
    """
    if repo and path:
        raise typer.BadParameter('cannot be combined with a repo name.', param_hint="'--path'")

    t0 = time.perf_counter()

    # counts is rendered rather than MofNCompleteColumn because a target's line carries a
    # third number: scanned races to the total on a re-index while almost nothing is
    # re-embedded, so the updated count is the one that says whether work is happening.
    progress = Progress(
        SpinnerColumn(),
        TextColumn('[bold]{task.description}[/bold]'),
        BarColumn(),
        TextColumn('{task.fields[counts]}'),
        TextColumn('{task.fields[current_file]}'),
        console=err_console,
    )

    def make_progress_callback(task_id: TaskID) -> service.ProgressCallback:
        def on_progress(update: service.IndexProgress) -> None:
            if update.to_update is None:
                # Still hashing: how much needs re-embedding is not known yet, so the bar
                # tracks the scan and says so rather than implying work is underway.
                progress.update(
                    task_id,
                    total=update.total,
                    completed=update.scanned,
                    counts=f'{format_counts(update.scanned, update.total)} scanned',
                    current_file=format_current_file(update.current_file),
                )
                return
            progress.update(
                task_id,
                total=update.to_update,
                completed=update.updated,
                counts=f'{format_counts(update.updated, update.to_update)} updated · {update.total} files',
                current_file=format_current_file(update.current_file),
            )

        return on_progress

    if path:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            err_console.print(f'[red]Path not found: {root}[/red]')
            raise typer.Exit(1)
        targets = [IndexTarget(name=root.name, path=root, kind='path')]
    elif repo:
        target = get_target_by_name(repo)
        if target is None:
            err_console.print(f'[red]No repo named {repo!r}.[/red]')
            advice = advise_on_a_path(repo)
            if advice:
                err_console.print(f'  {advice}')
            for suggestion in suggest_target_names(repo):
                err_console.print(f'  did you mean [bold]{suggestion}[/bold]?')
            raise typer.Exit(2)
        targets = [target]
    else:
        targets = all_index_targets()

    results = []
    # The session wraps the progress display rather than sitting inside it: both the copy it
    # opens with and the swap it closes with are silent multi-second pauses, and announcing
    # them from under a live progress bar puts them out of order with the work they bracket.
    try:
        with service.index_session(on_stage=announce_stage) as session, progress:
            # Per-target tasks are removed as they finish, so without a task spanning the
            # whole list the bar restarts silently and a long run reads as no progress.
            overall = (
                progress.add_task('all targets', total=len(targets), counts=format_counts(0, len(targets)), current_file='')
                if len(targets) > 1
                else None
            )
            for target in targets:
                task_id = progress.add_task(target.name, total=None, counts='', current_file='')
                result = session.index_target(target, on_progress=make_progress_callback(task_id))
                results.append(result)
                # Draw the target's closing tick before dropping its line, or it ends on the
                # count from before its last file and reads as one short of what it did.
                progress.refresh()
                progress.remove_task(task_id)
                if overall is not None:
                    progress.advance(overall)
                    progress.update(overall, counts=format_counts(len(results), len(targets)))

        for result in results:
            print_index_result(result)
        if len(results) > 1:
            total_updated = sum(r['files_updated'] for r in results)
            total_pruned = sum(r['files_pruned'] for r in results)
            total_chunks = sum(r['chunks_added'] for r in results)
            pruned = f', {total_pruned} deleted files removed' if total_pruned else ''
            console.print(f'\n[bold]Done.[/bold] {len(results)} repos, {total_updated} files updated, {total_chunks} chunks added{pruned}.')

    except KeyboardInterrupt:
        # The session has already swapped the partial index in by the time this runs. Say so:
        # stopping a run that has been going for hours needs to state what was kept, or the
        # next run offering to re-embed everything is the only answer you get.
        err_console.print('\n[bold]Stopped.[/bold] Everything embedded before the interrupt is in the index.')
        raise typer.Exit(130) from None

    elapsed = time.perf_counter() - t0
    err_console.print(f'Completed in {format_elapsed(elapsed)}')


def announce_stage(stage: str) -> None:
    """Narrate the two points where indexing stops to move the whole database.

    Both are silent multi-second pauses on an index this size, and an unexplained pause in a
    long-running command reads as a hang. What they say is what the run is doing to the
    index, never how it is done — a working copy and a rename are indy's business.
    'overlay busy' follows the save when the watcher holds its saved files, so the run says
    who reconciles them instead of ending silently with them in place.
    """
    if stage == 'snapshot':
        size = service.index_size_bytes()
        if size:
            err_console.print(f'Snapshotting the index ({format_size(size)})')
    elif stage == 'save':
        err_console.print('Saving the updated index')
    elif stage == 'overlay busy':
        err_console.print('`indy watch` is writing its saved files, so it reconciles them with this index')


def format_current_file(name: str) -> str:
    """The file being worked on, fenced off from the counts that precede it.

    Escaped because the corpus contains filenames with square brackets — a route file called
    `[slug].md` is markup to rich, and it would swallow the name it is meant to show.
    """
    return f'│ [green]{escape(name)}[/green]' if name else ''


def suggest_target_names(name: str) -> list[str]:
    """Names close enough to what was typed to be worth naming in the error.

    A bare name that an exemplar carries qualified is the common miss and is not a typo, so
    it is offered ahead of any fuzzy match: `fastapi` should point at `tiangolo/fastapi`.
    """
    names = [target.name for target in all_index_targets()]
    qualified = [candidate for candidate in names if candidate.rpartition('/')[2] == name]
    return qualified or difflib.get_close_matches(name, names, n=3, cutoff=0.6)


def advise_on_a_path(name: str) -> str | None:
    """The route back for a directory typed where a target name goes, or None.

    A directory a registry already covers has a name, and that name is the answer rather
    than `--path`: indexing the same tree by path labels it after its directory and leaves
    it in the index twice under two labels. Anything else on disk is what `--path` is for.
    """
    root = Path(name).expanduser()
    if not root.is_dir():
        return None
    resolved = root.resolve()
    for target in all_index_targets():
        if target.path.resolve() == resolved:
            return f'that path is [bold]{target.name}[/bold] — run [bold]indy index {target.name}[/bold]'
    return f'to index a path, run [bold]indy index --path {name}[/bold]'


def format_counts(done: int, total: int) -> str:
    """`  7/22`, padded so the number stops jittering as it climbs into another digit."""
    return f'{done:>{len(str(total))}}/{total}'


def format_size(num_bytes: int) -> str:
    gigabytes = num_bytes / 1_000_000_000
    if gigabytes >= 1:
        return f'{gigabytes:.1f} GB'
    return f'{num_bytes / 1_000_000:.0f} MB'


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f'{minutes}m {secs:.1f}s'


def format_age(seconds: float) -> str:
    """A duration at one unit — the precision a caveat about staleness is read at."""
    for size, unit in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if seconds >= size:
            count = int(seconds // size)
            return f'{count} {unit}' if count == 1 else f'{count} {unit}s'
    return 'under a minute'


def describe_freshness(freshness: dict) -> str:
    """One sentence bounding the age of everything the read could have matched, and one for the overlay above it."""
    oldest = format_age(freshness['oldest']['age_seconds'])
    newest = format_age(freshness['newest']['age_seconds'])
    if freshness['repos'] == 1:
        scanned = f'Index: {freshness["oldest"]["repo"]} scanned {oldest} ago.'
    elif oldest == newest:
        scanned = f'Index: {freshness["repos"]} repos, all scanned {oldest} ago.'
    else:
        scanned = f'Index: {freshness["repos"]} repos, scanned between {newest} and {oldest} ago.'
    live = freshness.get('overlay')
    if live is None:
        return scanned
    if not live['applied'] and live['files']:
        held = f'{live["files"]} saved file{"" if live["files"] == 1 else "s"}'
        return f'{scanned} Live: {held} set aside until `indy watch` reconciles the overlay with this index.'
    files = f'{live["files"]} file{"" if live["files"] == 1 else "s"} newer than the scan'
    updated = f', updated {format_age(live["age_seconds"])} ago' if 'age_seconds' in live else ''
    return f'{scanned} Live: {files}{updated}.'


def format_branch(row: dict) -> str:
    """` @branch` for a result from a linked worktree, or nothing for a main checkout's."""
    return f' [yellow]@{escape(row["branch"])}[/yellow]' if row.get('branch') else ''


def print_freshness(repo: str | None = None, owned: bool | None = None) -> None:
    """State how far behind the index is, so a miss is never read as an absence.

    Printed whether or not anything matched, and whether or not the index is old. A caveat
    that appears only past some threshold teaches the reader to take its absence for
    currency, which is the reading that made a stale answer indistinguishable from a true
    one in the first place.
    """
    freshness = service.index_freshness(repo=repo, owned=owned)
    if freshness is None:
        return
    console.print(f'[dim]{describe_freshness(freshness)}[/dim]')


def print_index_result(result: dict) -> None:
    status_str = f'[red]error: {result["error"]}[/red]' if result['error'] else '[green]ok[/green]'
    # Pruning is silent when there was nothing to prune, which is almost every run. A
    # standing "pruned 0" would train the eye past the one run that removed something.
    pruned = f'-{result["files_pruned"]} deleted, ' if result.get('files_pruned') else ''
    console.print(
        f'  {result["repo"]}: scanned {result["files_scanned"]}, '
        f'updated {result["files_updated"]}, '
        f'{pruned}'
        f'+{result["chunks_added"]} chunks — {status_str}'
    )
    scip = result.get('scip')
    if scip and scip['reason'] != 'no Python files':
        console.print(f'    {describe_scip(scip)}')


def describe_scip(state: dict) -> str:
    """One line on what scip-python did for a repo. A repo with no Python says nothing, since there was nothing to resolve."""
    if state['status'] == 'ok':
        if state.get('unchanged'):
            return '[dim]scip-python: unchanged since the last run[/dim]'
        return (
            f'[dim]scip-python: {state["reference_count"]} references across {state["document_count"]} files in {state["seconds"]}s[/dim]'
        )
    color = 'yellow' if state['status'] == 'skipped' else 'red'
    return f'[{color}]scip-python {state["status"]}: {escape(state["reason"] or "")}[/{color}]'


@indy_app.command(
    'errors',
    epilog=examples(
        ('indy errors', 'which files the last runs could not index, and why'),
        ('indy errors --json', 'the same rows, for a script'),
    ),
)
def errors(
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
) -> None:
    """Show all indexing errors, grouped by repo with per-file details."""
    error_details = service.get_status()['error_file_details']
    if as_json:
        # The key `status --json` carries the same rows under, so one filter reads both.
        print_json({'error_file_details': error_details})
        return

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


@indy_app.command(
    'errors-clear',
    epilog=examples(('indy errors-clear', 'empty the list once those files are fixed or deleted')),
)
def errors_clear():
    """Empty the list `indy errors` shows.

    Clearing retries nothing. An `indy index` run retries every failed file
    in the targets it covers, and records a file again if it fails again.
    """
    cleared = service.clear_errors(on_stage=announce_stage)
    if cleared:
        console.print(f'Cleared {cleared} error record{"s" if cleared != 1 else ""}.')
    else:
        console.print('No error records to clear.')


@indy_app.command(
    'forget',
    epilog=examples(
        ('indy forget old-name', 'drop a repo indexed under a name it no longer has'),
        ('indy repos', 'list the labels the index holds, to find the one to forget'),
    ),
)
def forget(
    repo: str = typer.Argument(..., help='The indexed repo label to remove.'),
):
    """Remove one repo label from the index, so its files are re-indexed under the current one."""
    if repo not in service.indexed_repo_names():
        raise typer.BadParameter(f'{repo!r} is not a label in the index. See the ones that are with `indy repos`.')

    removed = service.forget_repo(repo, on_stage=announce_stage)
    console.print(f'Forgot [bold]{repo}[/bold]: {removed["files"]} files, {removed["chunks"]} chunks, {removed["runs"]} runs.')
    console.print('Run [bold]indy index[/bold] to re-index whatever is still a target.')


@indy_app.command(
    'watch',
    epilog=examples(('indy watch', 'make each save searchable here before the next index')),
)
def watch():
    """Index every save on this machine within a second, until stopped.

    Runs in the foreground, for a service manager to keep alive. Each save in a repo, a linked
    worktree or an extra path goes into a local overlay that every read here sees on top of the
    index. Exemplar clones are left to `indy index`, which also clears from the overlay
    whatever the new index holds.
    """
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    service.watch(stop, on_change=print_live_change, on_note=print_note, on_problem=print_problem)
    err_console.print('Stopped.')


def clock() -> str:
    return time.strftime('%H:%M:%S')


# soft_wrap: under a service manager no terminal sets a width, and rich would break each line at 80.


def print_live_change(change: service.LiveChange) -> None:
    detail = {
        'indexed': f'{change.embedded} embedded, {change.reused} reused',
        'removed': 'deleted',
        'restored': 'matches the index again',
    }[change.action]
    line = f'{clock()} {change.action:<8} {escape(change.file_path)}{format_branch(vars(change))} · {detail} · {change.seconds:.2f}s'
    console.print(line, soft_wrap=True)


def print_note(message: str) -> None:
    err_console.print(f'{clock()} {escape(message)}', soft_wrap=True)


def print_problem(message: str) -> None:
    err_console.print(f'{clock()} [red]{escape(message)}[/red]', soft_wrap=True)


@indy_app.command(
    'search',
    epilog=examples(
        ('indy search "retry with backoff"', 'find code by what it does, not its name'),
        ('indy search "parse config" --owned -n 5', 'the top five, in your own repos'),
        ('indy search "connection pool" -l go --json', 'Go matches only, for a script'),
    ),
)
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
        raise typer.BadParameter('cannot be combined with --reference.', param_hint="'--owned'")
    ownership = True if owned else (False if reference else None)
    results = service.search(query, repo=repo, language=language, limit=limit, owned=ownership)

    if as_json:
        for r in results:
            r['score'] = round(1 - r.pop('distance'), 4)
        print_json({'results': results, 'freshness': service.index_freshness(repo=repo, owned=ownership)})
        return

    if not results:
        console.print('No results.')

    for i, r in enumerate(results, 1):
        score = 1 - r['distance']  # convert distance to similarity
        symbol = f' · {r["symbol_name"]}' if r.get('symbol_name') else ''
        console.print(f'\n[bold cyan]{i}.[/bold cyan] {r["file_path"]}{format_branch(r)}{symbol}  (score: {score:.2f})')
        snippet = r['text'][:300].replace('\n', '\n   ')
        console.print(f'   {snippet}')
        if len(r['text']) > 300:
            console.print('   …')

    print_freshness(repo=repo, owned=ownership)


@indy_app.command(
    'symbol',
    epilog=examples(
        ('indy symbol index_session', 'every definition of a name, in every repo'),
        ('indy symbol IndexSession --repo indy', 'the same, inside one repo'),
    ),
)
def symbol(
    name: str = typer.Argument(..., help='Exact function or class name to look up.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit to this repo.'),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Exact symbol lookup by function or class name — precise alternative to semantic search."""
    results = service.search_symbol(name, repo=repo)
    if as_json:
        print_json({'results': results, 'freshness': service.index_freshness(repo=repo)})
        return

    if not results:
        console.print(f'No symbol named {name!r} found.')

    for i, r in enumerate(results, 1):
        kind = f'{r["symbol_type"]} ' if r.get('symbol_type') else ''
        console.print(f'\n[bold cyan]{i}.[/bold cyan] {kind}[bold]{r["symbol_name"]}[/bold] — {r["file_path"]}{format_branch(r)}')
        snippet = r['text'][:300].replace('\n', '\n   ')
        console.print(f'   {snippet}')
        if len(r['text']) > 300:
            console.print('   …')

    print_freshness(repo=repo)


@indy_app.command(
    'deps',
    epilog=examples(
        ('indy deps index_session -d callers', 'what calls it, before you change it'),
        ('indy deps IndexSession.index_target', 'callers and callees of one method'),
    ),
)
def deps(
    symbol_name: str = typer.Argument(..., help="A bare name, or 'Owner.member' for a method or a module's function."),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit to this repo.'),
    direction: Literal['callers', 'callees', 'both'] = typer.Option('both', '--direction', '-d', help='Which side of the graph to list.'),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Find what calls a symbol or what it calls, using the Python reference graph.

    Each caller says how it matched: call, reference (used as a value), self, typed (a
    parameter annotated with a base class or a subclass of the owner), module (a bare name in
    the owner's own file), or candidate. A candidate is a call by method name on an untyped
    receiver, listed after every resolved caller.

    In a repo the last full index ran scip-python over, callers come from its type checker.
    Two more labels appear there: inferred (a call on a receiver only a type checker types,
    as a local assigned from a call), and override (a call to a method this one overrides).
    """
    result = service.get_dependencies(symbol_name, repo=repo, direction=direction)
    if as_json:
        print_json({**result, 'freshness': service.index_freshness(repo=repo)})
        return

    callers = result.get('callers', [])
    callees = result.get('callees', [])
    if not callers and not callees:
        console.print(f'No references found for {symbol_name!r}. (Reference graph is Python-only.)')
    else:
        if 'callers' in result:
            candidates = sum(c['via'] == 'candidate' for c in callers)
            counted = f'{len(callers) - candidates} resolved, {candidates} candidates' if candidates else str(len(callers))
            console.print(f'\n[bold]Callers of {escape(symbol_name)}[/bold] ({counted})')
            if result['resolved_by_scip']:
                console.print(f'[dim]  Resolved by scip-python in {", ".join(result["resolved_by_scip"])}.[/dim]')
            if not callers:
                console.print('  (none)')
            for c in callers:
                caller = f'[cyan]{escape(c["source_symbol"] or "?")}[/cyan]'
                at = f':{c["line"]}' if c.get('line') else ''
                console.print(f'  [magenta]{c["via"]:<9}[/magenta] {caller} — {c["source_file"]}{at}{format_branch(c)}')

        if 'callees' in result:
            console.print(f'\n[bold]Callees of {escape(symbol_name)}[/bold] ({len(callees)})')
            if not callees:
                console.print('  (none)')
            for c in callees:
                module_prefix = f'{c["target_module"]}.' if c.get('target_module') else ''
                console.print(f'  [cyan]{module_prefix}{c["target_symbol"]}[/cyan] — {c["source_file"]}{format_branch(c)}')

    print_freshness(repo=repo)


@indy_app.command(
    'stats',
    epilog=examples(
        ('indy stats', 'chart which repos take up the index'),
        ('indy stats indy', 'chart how much each recent scan of one repo changed'),
        ('indy stats --json', 'the same sizes, for a script'),
    ),
)
def stats(
    repo: str = typer.Argument(None, help='Show scan history for a specific repo.'),
    as_json: bool = typer.Option(False, '--json', help='Output as JSON to stdout.'),
):
    """Show index size per repo with charts, or scan history for a specific repo."""
    if repo:
        history = service.repo_scan_history(repo)
        if not history and get_target_by_name(repo) is None:
            err_console.print(f'[red]Repo {repo!r} not found. Exemplar clones are named owner/repo.[/red]')
            raise typer.Exit(1)
        # A known repo that was never scanned is an empty answer, not a failure, at either door.
        if as_json:
            print_json({'repo': repo, 'scans': history})
            return
        if not history:
            console.print(f'No scan history for {repo!r}. Run [bold]indy index {repo}[/bold] first.')
            return

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
    if as_json:
        # The shape `repos --json` emits, since both read the same rows.
        print_json({'repos': repo_list})
        return
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


@indy_app.command(
    'repos',
    epilog=examples(
        ('indy repos', 'every indexed repo, its size, and when it was last indexed'),
        ('indy repos --json', 'the same list, for a script'),
    ),
)
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


config_app = typer.Typer(
    name='config',
    no_args_is_help=True,
    rich_markup_mode='markdown',
    help='Inspect the resolved settings.',
    epilog=examples(('indy config show', 'which paths indy reads, and what set each one')),
)
indy_app.add_typer(config_app)


@config_app.command(
    'show',
    epilog=examples(
        ('indy config show', 'which paths indy reads, and what set each one'),
        ('indy config show --json', 'the same settings, for a script'),
    ),
)
def config_show(
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


@indy_app.command(
    'update',
    epilog=examples(
        ('indy update --check', 'whether a newer release exists, installing nothing'),
        ('indy update', 'install the latest release'),
    ),
)
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


def main() -> None:
    """Console entry point.

    Wraps the app so a machine with no index yet — the ordinary state of one that searches a
    snapshot a peer has not delivered — gets the sentence telling it what to do, rather than
    a traceback out of the connection layer.

    A fault in config.toml or $INDY_EXTRA_PATHS is found at import, before the app exists, so
    it is checked first. Every command resolves its paths from those settings, so none of
    them runs.
    """
    if config.CONFIG_FAULT:
        err_console.print(f'[red]{escape(str(config.CONFIG_FAULT))}[/red]', soft_wrap=True)
        sys.exit(1)
    try:
        indy_app()
    except IndexNotFoundError as exc:
        err_console.print(f'[red]{escape(str(exc))}[/red]', soft_wrap=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
