# indy

Semantic search index for local codebases, docs, and notes.
Local-first: sqlite-vec + ollama. No external APIs, no separate vector DB process.

## Architecture

```yaml
storage.py     Pure SQLite + sqlite-vec access. No business logic.
               Only layer that touches the DB or serializes embeddings.
service.py     All meaningful operations: index_path, search, search_symbol, refresh,
               get_dependencies. The CLI is a thin consumer of this layer.
chunker.py     Chunking strategies per content type:
               - Python: AST (function/class/method boundaries)
               - Go/TS/TSX/Rust: tree-sitter AST (same granularity)
               - Prose (md/rst/txt): paragraph-aware splitter
               - Config (yaml/toml/json): whole-file if small, code-split if large
               - Other code: recursive character splitter
repos.py       Reads ~/dev/repos.json, returns active repos + configured extra paths.
               Resolves repo ownership (owned vs reference) from per-repo owner field.
config.py      Constants and env var overrides (INDY_DIR, OLLAMA_HOST, OLLAMA_MODEL, etc.).
charts.py      Terminal chart primitives (horizontal bars, vertical bars, streamline) using Rich + Unicode.
main.py        CLI entry points only — thin Typer shells calling service.py.
```

**service.py is the API.** All callers go through service.py. Never call storage.py directly from main.py.

## Storage

Three tables + one virtual table in `~/.local/share/indy/indy.db` (SQLite, WAL mode):

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

Override data dir: `INDY_DIR` env var (default `$XDG_DATA_HOME/indy/`, i.e. `~/.local/share/indy/`).

## Embeddings

Ollama REST API at `http://localhost:11434`. Model: `nomic-embed-text` (768-dim, cosine similarity).
Override: `OLLAMA_HOST`, `OLLAMA_MODEL`, `EMBEDDING_DIM`.

Ollama must be running before any index or search operation. Start with `ollama serve` or
`brew services start ollama`. Model weights stored at `$XDG_DATA_HOME/ollama/models`.

## Repo Ownership

repos.json has a top-level `owner` field (default: `datapointchris`). Each repo can optionally
set its own `owner`. Repos matching the top-level owner (or with no explicit owner) are "owned";
repos with a different owner are "reference" — third-party code cloned for study.

Search supports `--owned` / `--reference` flags to filter by ownership. Default is to search all repos.

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
- **No git post-commit hook** — on-demand `indy index` + forge die covers the use case.
  Per-repo hook management adds maintenance overhead for marginal gain.
- **symbol_reference is Python-only** — tree-sitter already parses Go/TS/Rust for chunking;
  reference extraction for those languages is deferred. Python AST covers the primary use case.
- **target_module captured for attribute calls** — `storage.get_db()` stores target_module="storage",
  helping disambiguate when multiple functions share a name across modules.
- **Ownership resolved at service layer, not storage** — repos.json maps repo name → owner.
  service.py resolves `--owned`/`--reference` into a set of repo names and passes it to storage
  as a generic filter. Storage knows nothing about ownership.

## CLI

```bash
indy index                     # index all active repos + extra paths
indy index ichrisbirch         # single repo by name
indy index --path ~/dir/       # arbitrary path (uses dirname as label)
indy search "query"            # semantic search
indy search "q" --repo R --language python --limit 20
indy search "q" --owned        # only your repos (owner matches top-level)
indy search "q" --reference    # only reference repos (owner differs)
indy symbol get_db             # exact symbol lookup by function/class name
indy deps get_db               # reference graph: callers + callees of a symbol
indy deps get_db -d callers    # only callers (or -d callees)
indy search "q" --json         # machine-readable JSON (also on status/repos/symbol/deps)
indy update                    # reinstall latest from GitHub
indy status                    # health dashboard: totals, recent runs, error count
indy errors                    # per-file error listing grouped by repo
indy errors-clear              # remove error records (re-attempted on next index)
indy stats                     # bar charts: files/chunks per repo, last indexed
indy stats ichrisbirch         # scan history charts for a specific repo
indy repos                     # per-repo file/chunk counts (table)
```

Agents consume indy by shelling out to the CLI (e.g. `indy search "q" --json`), not via an MCP
server. Read commands (`search`, `status`, `repos`, `symbol`, `deps`) take `--json` for
machine-readable output.

## Batch Re-indexing

```bash
forge dies run indy-index-all          # incremental re-index across all active repos
forge dies run indy-index-all -F indy  # single repo only
```
