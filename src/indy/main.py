import typer

indy_app = typer.Typer(name='indy', no_args_is_help=True, help='Semantic search index for local codebases, docs, and notes.')


@indy_app.command('status')
def status():
    """Show index health: repos indexed, file counts, last run."""
    typer.echo('indy: not yet implemented')


@indy_app.command('index')
def index(
    path: str = typer.Argument(None, help='Path to index. Omit to index all repos from repos.json.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Repo name from repos.json.'),
    all_repos: bool = typer.Option(False, '--all', help='Index all active repos from repos.json.'),
):
    """Index a path or repo into the semantic search store."""
    typer.echo('indy index: not yet implemented')


@indy_app.command('search')
def search(
    query: str = typer.Argument(..., help='Natural language search query.'),
    repo: str = typer.Option(None, '--repo', '-r', help='Limit search to this repo.'),
    language: str = typer.Option(None, '--language', '-l', help='Limit to file language (python, go, markdown…).'),
    limit: int = typer.Option(10, '--limit', '-n', help='Number of results to return.'),
):
    """Semantic search across indexed code and notes."""
    typer.echo('indy search: not yet implemented')


@indy_app.command('mcp')
def mcp():
    """Show MCP server setup command."""
    typer.echo('Add to ~/.claude/settings.json:')
    typer.echo('  claude mcp add indy -- python -m indy.mcp')


if __name__ == '__main__':
    indy_app()
