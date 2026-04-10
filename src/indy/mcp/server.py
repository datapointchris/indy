import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP('indy')


@mcp.tool()
def indy_status() -> str:
    """Show index health: repos indexed, file counts, last run, any errors."""
    return json.dumps({'status': 'not yet implemented'})


@mcp.tool()
def indy_search(query: str, repo: str | None = None, language: str | None = None, limit: int = 10) -> str:
    """Semantic search across indexed code and notes.
    Returns ranked chunks with file path, symbol name, and snippet.
    Use repo to narrow to a specific codebase. Use language to filter by file type."""
    return json.dumps({'results': [], 'message': 'not yet implemented'})


@mcp.tool()
def indy_search_symbol(name: str, repo: str | None = None) -> str:
    """Exact symbol lookup by function or class name.
    More precise than semantic search for known symbol names."""
    return json.dumps({'results': [], 'message': 'not yet implemented'})


@mcp.tool()
def indy_get_file(file_path: str) -> str:
    """Return full contents of an indexed file.
    Use after search to get surrounding context around a matched chunk."""
    return json.dumps({'content': None, 'message': 'not yet implemented'})


@mcp.tool()
def indy_list_repos() -> str:
    """List all indexed repos with file counts and last index time."""
    return json.dumps({'repos': [], 'message': 'not yet implemented'})


@mcp.tool()
def indy_refresh(repo: str | None = None) -> str:
    """Trigger incremental re-index of a repo or all repos.
    Only re-embeds changed files. Safe to call during a session."""
    return json.dumps({'message': 'not yet implemented'})
