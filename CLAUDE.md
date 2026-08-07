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
repos.py       Reads both registries and returns IndexTarget records (name, path, kind,
               exclude). repos_file = the portfolio; exemplar_repos_file = third-party
               clones kept as code exemplars. Also owns is_excluded().
config.py      XDG base dirs, config.toml loading, and constants. Every setting resolves
               environment variable → config.toml → built-in default.
charts.py      Terminal chart primitives (horizontal bars, vertical bars, streamline) using Rich + Unicode.
main.py        CLI entry points only — thin Typer shells calling service.py.
```

**service.py is the API.** All callers go through service.py. Never call storage.py directly from main.py.

## Storage

Three tables + one virtual table in `$XDG_DATA_HOME/indy/indy.db` (SQLite, WAL mode):

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

Override the data dir with `data_dir` in config.toml or the `INDY_DIR` env var.

The WAL is checkpointed (`PRAGMA wal_checkpoint(TRUNCATE)`) at the end of every index
run, so `indy.db` is a complete copy on its own. Without that, anything replicating the
file without its `-wal` sidecar silently lands on the state before the last run.

## Embeddings

Ollama REST API at `http://localhost:11434`. Model: `nomic-embed-text` (768-dim, cosine similarity).
Override: `OLLAMA_HOST`, `OLLAMA_MODEL`, `EMBEDDING_DIM`.

Ollama must be running before any index or search operation. Start with `ollama serve` or
`brew services start ollama`. Model weights stored at `$XDG_DATA_HOME/ollama/models`.

## Two Registries

`repos_file` holds the portfolio — repos we work in. `exemplar_repos_file` holds third-party
clones kept to be *read* as examples of specific patterns; each entry carries `exemplary_for`
(what it demonstrates) and `index_exclude` (subtrees to keep out of the index). They are
separate files because the two answer different questions and need different fields.

**Exemplars are indexed under a qualified `owner/name`.** Bare names collide across the two
registries — a clone commonly shares its name with a repo you own — which previously merged
both repos' chunks under one label and put that name in *both* the owned and reference sets,
so `--owned` returned a stranger's code.

Search supports `--owned` / `--reference` to filter. Default is to search everything.

## What Gets Indexed

Active repos from `repos_file`, exemplar clones from `exemplar_repos_file`, plus any
`[[extra_paths]]` in config.toml. All three default to nothing outside indy's own XDG dirs —
a default that names a particular machine's layout is the bug this arrangement exists to
prevent, and it is why no directory of personal notes appears anywhere in this repo.

Chunk paths are stored `~`-relative (`compact_path`), so one index file is portable across
machines and platforms — which is what makes "index on one machine, search from all of them"
work when `data_dir` points at a replicated tree.

File inclusion: `.py .go .js .ts .tsx .sh .rs .rb` (code), `.md .rst .txt` (prose), `.yaml .yml .toml .json` (config).
Always skip: `.git/ node_modules/ __pycache__ .venv/ dist/ build/` dirs; `*.lock` files; files > 500KB.
For git repos, `.gitignore` is respected via `git ls-files`. Non-git paths fall back to directory walking.

Per-target `index_exclude` patterns apply on top, using gitignore semantics: patterns in order,
leading `!` re-includes, last match wins. This is what a global skip-list cannot express — FastAPI
ships 14 doc translations, so `docs/*/**` then `!docs/en/**` keeps the 154 English pages and drops
1501 near-duplicates. Across the exemplar corpus these patterns cut 33% of indexed files.

## Key Decisions

- **Cosine distance in vec0** — nomic-embed-text embeddings are not unit-normalized; cosine gives
  correct [0,1] similarity scores. L2 would produce incorrect rankings for non-normalized vectors.
- **KNN oversample + Python filter** — vec0 MATCH can't filter on metadata; oversample 20× then
  filter by repo/language in Python.
- **No git post-commit hook** — an on-demand `indy index`, scheduled or run by hand, covers the
  use case. Per-repo hook management adds maintenance overhead for marginal gain.
- **symbol_reference is Python-only** — tree-sitter already parses Go/TS/Rust for chunking;
  reference extraction for those languages is deferred. Python AST covers the primary use case.
- **target_module captured for attribute calls** — `storage.get_db()` stores target_module="storage",
  helping disambiguate when multiple functions share a name across modules.
- **Ownership resolved at service layer, not storage** — service.py resolves `--owned`/`--reference`
  into a set of repo names and passes it to storage as a generic filter. Storage knows nothing
  about ownership.
- **No default names a path outside indy's own XDG dirs** — where a registry lives and which loose
  directories are worth indexing are properties of a machine, so they are config.toml keys, not
  constants. A default naming someone's layout is both wrong for everyone else and a way for
  personal detail to end up in a public repo.
- **Settings resolve env var → config.toml → default, and report which layer won** — an index that
  searches nothing is almost always a config file that was never read rather than a wrong path, so
  `indy config` prints the source next to the value.
- **Exemplars live in their own registry, not an `owner` field on repos.json** — an exemplar needs
  fields a worked-in repo has no business carrying (`exemplary_for`, `index_exclude`), and keeping
  them in one file forced an owner-as-marker hack that still left the name collision unsolved.

## CLI

```bash
indy index                     # index all active repos + extra paths
indy index myrepo              # single repo by name
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
indy stats myrepo              # scan history charts for a specific repo
indy repos                     # per-repo file/chunk counts (table)
indy config                    # resolved settings and the layer each came from
```

Agents consume indy by shelling out to the CLI (e.g. `indy search "q" --json`), not via an MCP
server. Read commands (`search`, `status`, `repos`, `symbol`, `deps`) take `--json` for
machine-readable output.
