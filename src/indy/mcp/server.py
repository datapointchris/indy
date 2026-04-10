import json

from mcp.server.fastmcp import FastMCP

import indy.service as service

mcp = FastMCP('indy')


@mcp.tool()
def indy_status() -> str:
    """Show index health: total files indexed, total chunks, last run stats, error count."""
    return json.dumps(service.get_status())


@mcp.tool()
def indy_search(query: str, repo: str | None = None, language: str | None = None, limit: int = 10) -> str:
    """Semantic search across indexed code and notes.
    Returns ranked chunks with file path, symbol name, snippet, and similarity score.
    Use repo to narrow to a specific codebase. Use language to filter by file type (python, go, markdown…)."""
    results = service.search(query, repo=repo, language=language, limit=limit)
    for r in results:
        r['score'] = round(1 - r.pop('distance'), 4)
    return json.dumps({'results': results})


@mcp.tool()
def indy_search_symbol(name: str, repo: str | None = None) -> str:
    """Exact symbol lookup by function or class name.
    More precise than semantic search when you know the symbol name.
    Use repo to narrow to a specific codebase."""
    results = service.search_symbol(name, repo=repo)
    return json.dumps({'results': results})


@mcp.tool()
def indy_get_file(file_path: str) -> str:
    """Return full contents of an indexed file.
    Use after search to get full context around a matched chunk."""
    content = service.get_file_content(file_path)
    if content is None:
        return json.dumps({'content': None, 'error': f'File not found: {file_path}'})
    return json.dumps({'content': content})


@mcp.tool()
def indy_list_repos() -> str:
    """List all indexed repos with file counts, chunk counts, and last index time."""
    return json.dumps({'repos': service.list_repos()})


@mcp.tool()
def indy_refresh(repo: str | None = None) -> str:
    """Trigger incremental re-index of a repo or all repos.
    Only re-embeds files whose content has changed. Safe to call during a session."""
    return json.dumps(service.refresh(repo=repo))
