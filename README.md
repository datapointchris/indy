# indy

Semantic search index for local codebases, docs, and notes.

Local-first: sqlite-vec for vector storage, ollama for embeddings. No external APIs.

## Usage

```bash
indy index                     # index all active repos from ~/dev/repos.json
indy index ichrisbirch         # index a single repo by name
indy index --path ~/notes/dev/ # index an arbitrary path
indy search "query"            # semantic search across all indexed content
indy search "query" --repo indy --language python
indy search "query" --owned    # only your repos
indy search "query" --reference # only reference repos
indy status                    # index health: file counts, last run
indy errors                    # per-file error listing grouped by repo
indy errors-clear              # remove error records (re-attempted on next index)
indy stats                     # bar charts: files/chunks per repo, last indexed
indy stats ichrisbirch         # scan history charts for a specific repo
indy repos                     # list indexed repos with chunk counts
indy update                    # reinstall latest from GitHub
```

## Architecture

```yaml
storage.py     SQLite manifest (indexed_file, index_run, symbol_reference) + sqlite-vec vectors
service.py     All logic: index_path, search, search_symbol, refresh, get_dependencies
chunker.py     Python AST chunking + tree-sitter (Go/TS/Rust) + prose/code splitters
               + Python reference extraction (call graph)
repos.py       Reads ~/dev/repos.json, returns active repos with resolved paths.
               Resolves repo ownership (owned vs reference) from per-repo owner field.
config.py      Constants and env var overrides (INDY_DIR, OLLAMA_HOST, etc.)
main.py        Thin Typer CLI wrappers over service.py
mcp/server.py  FastMCP tools: indy_search, indy_search_symbol, indy_get_file,
               indy_list_repos, indy_status, indy_refresh, indy_get_dependencies
```

## MCP Setup

```bash
claude mcp add indy -- indy-mcp
```

## Data

- DB: `~/dev/indy/indy.db` (override with `INDY_DIR`)
- Embeddings: ollama `nomic-embed-text` at `http://localhost:11434` (override with `OLLAMA_HOST`, `OLLAMA_MODEL`)
