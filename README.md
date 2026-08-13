# indy

Semantic search index for local codebases, docs, and notes.

Local-first: sqlite-vec for vector storage, ollama for embeddings. No external APIs.

## Requirements

- Python 3.13+
- [ollama](https://ollama.com) running locally with an embedding model:
  `ollama serve` and `ollama pull nomic-embed-text`

## Usage

```bash
indy index                      # index every active repo plus any configured extra paths
indy index myrepo               # index a single repo by name
indy index --path ~/writing/    # index an arbitrary path
indy search "query"             # semantic search across all indexed content
indy search "query" --repo indy --language python
indy search "query" --owned     # only repos from your own registry
indy search "query" --reference # only third-party exemplar clones
indy search "query" --json      # machine-readable JSON output
indy symbol get_db              # exact symbol lookup by function/class name
indy deps get_db                # reference graph: callers + callees of a symbol
indy config                     # resolved settings and which layer supplied each
indy status                     # index health: file counts, last run
indy errors                     # per-file error listing grouped by repo
indy errors-clear               # remove error records (re-attempted on next index)
indy forget notes-dev           # drop a stale label, so its files re-index under the current one
indy stats                      # bar charts: files/chunks per repo, last indexed
indy stats myrepo               # scan history charts for a specific repo
indy repos                      # list indexed repos with chunk counts
indy update                     # reinstall latest from GitHub
```

## Configuration

indy runs on defaults alone. To point it at a repo registry or extra directories,
create `$XDG_CONFIG_HOME/indy/config.toml` (usually `~/.config/indy/config.toml`):

```toml
# Repo registries. Default to indy's own config dir; name a shared path here when
# other tools read the same registry.
repos_registry = "~/registries/repos.json"
exemplar_registry = "~/registries/exemplar-repos.json"

# Where the index is written. Defaults to $XDG_DATA_HOME/indy. Point it at a synced
# tree to index on one machine and search from all of them.
# data_dir = "~/.local/share/indy"

ollama_host = "http://localhost:11434"
ollama_model = "nomic-embed-text"

# Non-repo roots to index, each with the label search results are grouped under.
[[extra_paths]]
name = "notes"
path = "~/notes"
```

Every setting resolves as **environment variable → `config.toml` → built-in default**.
The env vars are `INDY_DIR`, `INDY_REPOS_REGISTRY`, `INDY_EXEMPLAR_REGISTRY`,
`INDY_EXTRA_PATHS` (JSON), `OLLAMA_HOST`, and `OLLAMA_MODEL`. `indy config` prints
what each one resolved to and where it came from.

### Registries

`repos_registry` is a JSON registry of repos you own; `exemplar_registry` holds
third-party clones kept to be read as examples. They are separate files because
an exemplar carries fields a worked-in repo has no business having, and because a
clone often shares its bare name with a repo you own — exemplars are therefore
indexed under a qualified `owner/name`.

```json
{
  "repos": [
    { "name": "myrepo", "path": "~/code/myrepo", "status": "active" }
  ]
}
```

```json
{
  "repos": [
    {
      "name": "fastapi",
      "path": "~/code/refs/fastapi",
      "owner": "fastapi",
      "index_exclude": ["docs/*/**", "!docs/en/**"]
    }
  ]
}
```

`index_exclude` uses gitignore semantics — patterns apply in order, a leading `!`
re-includes, and the last match wins. That ordering is what a global skip-list
cannot express: FastAPI ships 14 doc translations, so `docs/*/**` then `!docs/en/**`
indexes the English pages and drops the near-duplicates of them.

## What gets indexed

Code (`.py .go .js .ts .tsx .sh .rs .rb`), prose (`.md .rst .txt`), and config
(`.yaml .yml .toml .json`). Always skipped: `.git/ node_modules/ __pycache__ .venv/
dist/ build/`, `*.lock` files, and anything over 500 KB. In a git repo `.gitignore`
is respected via `git ls-files`; other paths fall back to a directory walk.

## Architecture

```yaml
storage.py     SQLite manifest (indexed_file, index_run, symbol_reference) + sqlite-vec vectors
service.py     All logic: index_path, search, search_symbol, refresh, get_dependencies
chunker.py     Python AST chunking + tree-sitter (Go/TS/Rust) + prose/code splitters
               + Python reference extraction (call graph)
repos.py       Reads both registries, returns index targets with resolved paths.
               Resolves ownership (owned vs reference) from which registry a repo came from.
config.py      XDG paths, config.toml loading, and constants
main.py        Thin Typer CLI wrappers over service.py
```

## Data

- Index: `$XDG_DATA_HOME/indy/indy.db` (override with `data_dir` or `INDY_DIR`)
- Embeddings: ollama `nomic-embed-text` at `http://localhost:11434`

Chunk paths are stored `~`-relative, so one index file is portable across machines
and platforms. The WAL is checkpointed at the end of every index run, so `indy.db`
is a complete copy on its own — safe to back up or replicate without its sidecars.

## License

MIT
