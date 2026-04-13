# indy

Semantic search index for local codebases, docs, and notes.
Local-first: sqlite-vec + ollama. No external APIs, no separate vector DB process.

## Architecture

```yaml
storage.py     Pure SQLite + sqlite-vec access. No business logic.
               Only layer that touches the DB or serializes embeddings.
service.py     All meaningful operations: index_path, search, search_symbol, refresh,
               get_dependencies. CLI and MCP are thin consumers of this layer.
chunker.py     Chunking strategies per content type:
               - Python: AST (function/class/method boundaries)
               - Go/TS/TSX/Rust: tree-sitter AST (same granularity)
               - Prose (md/rst/txt): paragraph-aware splitter
               - Config (yaml/toml/json): whole-file if small, code-split if large
               - Other code: recursive character splitter
repos.py       Reads ~/dev/repos.json, returns active repos + configured extra paths.
config.py      Constants and env var overrides (INDY_DIR, OLLAMA_HOST, OLLAMA_MODEL, etc.).
main.py        CLI entry points only — thin Typer shells calling service.py.
mcp/server.py  FastMCP tools — thin wrappers calling service.py.
```

**service.py is the API.** All callers (CLI, MCP) go through service.py. Never call storage.py directly from main.py or mcp/server.py.

## Storage

Three tables + one virtual table in `~/dev/indy/indy.db` (SQLite, WAL mode):

```yaml
indexed_file      Manifest: one row per file. Tracks mtime, sha256 hash, chunk count, status.
                  Re-indexing is skipped when content_hash is unchanged.
index_run         Audit trail: one row per index run with file/chunk counts and errors.
chunk             Chunk text + metadata (file_path, repo, language, symbol_name, symbol_type, module).
vec_chunks        sqlite-vec virtual table. rowid matches chunk.id.
                  distance_metric=cosine — required for nomic-embed-text.
symbol_reference  Call graph: one row per reference (import/call/inherit) extracted during indexing.
                  Columns: source_file, source_symbol, target_symbol, target_module, ref_type.
                  Python-only. Indexed on source_file and target_symbol for fast caller/callee lookup.
```

Override data dir: `INDY_DIR` env var (default `~/dev/indy/`).

## Embeddings

Ollama REST API at `http://localhost:11434`. Model: `nomic-embed-text` (768-dim, cosine similarity).
Override: `OLLAMA_HOST`, `OLLAMA_MODEL`, `EMBEDDING_DIM`.

Ollama must be running before any index or search operation. Start with `ollama serve` or
`brew services start ollama`. Model weights stored at `$XDG_DATA_HOME/ollama/models`.

## What Gets Indexed

Active repos from `~/dev/repos.json` plus paths in `EXTRA_PATHS_RAW` (default: `~/notes/dev/`).

File inclusion: `.py .go .js .ts .tsx .sh .rs .rb` (code), `.md .rst .txt` (prose), `.yaml .yml .toml .json` (config).
Always skip: `.git/ node_modules/ __pycache__ .venv/ dist/ build/` dirs; `*.lock` files; files > 500KB.
For git repos, `.gitignore` is respected via `git ls-files`. Non-git paths fall back to directory walking.

## Key Decisions

- **Cosine distance in vec0** — nomic-embed-text embeddings are not unit-normalized; cosine gives
  correct [0,1] similarity scores. L2 would produce incorrect rankings for non-normalized vectors.
- **KNN oversample + Python filter** — vec0 MATCH can't filter on metadata; oversample 20× then
  filter by repo/language in Python.
- **get_file_content reads from disk** — MCP `indy_get_file` returns current file contents, not
  stored chunk text. Always up to date; no storage overhead.
- **No git post-commit hook** — on-demand `indy index` + forge die covers the use case.
  Per-repo hook management adds maintenance overhead for marginal gain.
- **symbol_reference is Python-only** — tree-sitter already parses Go/TS/Rust for chunking;
  reference extraction for those languages is deferred. Python AST covers the primary use case.
- **target_module captured for attribute calls** — `storage.get_db()` stores target_module="storage",
  helping disambiguate when multiple functions share a name across modules.

## CLI

```bash
indy index                     # index all active repos + extra paths
indy index --repo ichrisbirch  # single repo by name
indy index ~/path/             # arbitrary path (uses dirname as label)
indy search "query"            # semantic search
indy search "q" --repo R --language python --limit 20
indy status                    # health + error file details
indy clear-errors              # remove error records (re-attempted on next index)
indy repos                     # per-repo file/chunk counts
```

## MCP

```bash
claude mcp add indy -- python -m indy.mcp
```

Tools: `indy_search`, `indy_search_symbol`, `indy_get_file`, `indy_list_repos`, `indy_status`, `indy_refresh`, `indy_get_dependencies`.

## Batch Re-indexing

```bash
forge dies run indy-index-all          # incremental re-index across all active repos
forge dies run indy-index-all -F indy  # single repo only
```
