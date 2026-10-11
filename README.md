# indy

Semantic search index for local codebases, docs, and notes.

Local-first: sqlite-vec for vector storage, ollama for embeddings. No external APIs.

## Requirements

- Python 3.13+
- [ollama](https://ollama.com) running locally with an embedding model:
  `ollama serve` and `ollama pull nomic-embed-text`
- Optionally, Node.js with `npx`, for exact Python references (below)

## Usage

```bash
indy index                      # index every active repo plus any configured extra paths
indy index myrepo               # index a single repo by name
indy index --path ~/writing/    # index an arbitrary path
indy watch                      # index every save within a second, until stopped
indy search "query"             # semantic search across all indexed content
indy search "query" --repo indy --language python
indy search "query" --owned     # only repos from your own registry
indy search "query" --reference # only third-party exemplar clones
indy search "query" --json      # machine-readable JSON output
indy symbol get_db              # exact symbol lookup by function/class name
indy deps get_db                # reference graph: callers + callees of a symbol
indy deps Store.save -d callers # Owner.member: exact where scip-python ran, else self calls, typed params, module hints
indy config show                # resolved settings and which layer supplied each
indy status                     # index health: file counts, last run
indy errors                     # per-file error listing grouped by repo
indy errors-clear               # remove error records (re-attempted on next index)
indy forget notes-dev           # drop a stale label, so its files re-index under the current one
indy stats                      # bar charts: files/chunks per repo, last indexed
indy stats myrepo               # scan history charts for a specific repo
indy repos                      # list indexed repos with chunk counts
indy update                     # reinstall latest from GitHub
```

`search`, `symbol` and `deps` each close with how far behind the index is, and `--json`
carries the same thing as a `freshness` object. Indexing is manual, so a symbol written
today is absent from an index scanned last week and a flat "no symbol named X" cannot be
told from one that does not exist. The number quoted is the *oldest* scan the read could
have matched, not the newest: indexing one repo makes the newest minutes old while every
other label stays where it was. Where `indy watch` is running, the line also says how many
files are newer than the scan, and when the watcher last wrote. Where the index was replaced
after the watcher last compared its saves with it, the line says those saves are set aside.

## Live indexing

`indy watch` runs in the foreground until stopped, for a service manager to keep alive. Each
save is indexed into a local overlay that every read on that machine sees on top of the
index, so `search`, `symbol` and `deps` return it about a second later. A deleted file drops
out of results the same way.

It watches every registered repo and extra path, and the linked worktrees of each repo
(`git worktree list`). A worktree file is indexed under its repo's name, only where it
differs from the main checkout, and each result from one shows `@branch`. Exemplar clones
are left to `indy index`.

Each index has its own overlay under `$XDG_CACHE_HOME/indy/overlays/`, outside the data
directory, so a setup that syncs the index never carries it. `indy config show` prints its
path. The overlay is compacted against the index whenever a new one is built or arrives,
and every file the index now holds drops out of it. Until that happens, a read sets the
overlay aside and says so, rather than letting older saves hide the newer index.

## Exact Python references

`indy index` runs [scip-python](https://github.com/sourcegraph/scip-python), Pyright's
indexer, over each Python repo in your own registry, pinned at 0.6.6 and run through `npx`.
Where it ran, `deps` takes that repo's callers from the type checker. That reaches a call on
a local whose type is only inferred, labeled `inferred`, and a call to a method an override
replaces, labeled `override`. A call by method name that resolves to some other class no
longer shows as a candidate.

It runs from the repo's `.venv`, with the project installed there. A repo without one is
skipped, since scip-python cannot name the project's modules without it. A repo whose run
fails keeps none of its earlier results, and `deps` answers for it by the syntactic rules.
`indy index` prints what happened per repo and `indy status` lists every repo it skipped,
with the reason. A repo is run again only once a Python file or an installed package changes.

`indy watch` never runs it, because a run takes about twenty seconds for a large repo. A
file saved since the last index answers from the syntactic rules until the next one.

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
`INDY_EXTRA_PATHS` (JSON), `OLLAMA_HOST`, and `OLLAMA_MODEL`. `indy config show` prints
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

Code in Python, Go, Rust, Ruby, Lua, shell and bats, JavaScript and TypeScript,
Vue and Astro with their HTML, CSS and SCSS, Terraform, SQL, and `Dockerfile`; prose
(`.md .rst .txt`); and config (`.yaml .yml .toml .json`). The exact suffixes and names
are `CODE_EXTENSIONS`, `CODE_FILENAMES`, `DOC_EXTENSIONS` and `CONFIG_EXTENSIONS` in
`src/indy/config.py`.

A file with no suffix is indexed when its first line is a shebang running `sh`, `bash`,
`zsh`, Python, or `uv run`. Only that line is read to decide.

Always skipped: `.git/ node_modules/ __pycache__ .venv/ dist/ build/`, lock files, and
anything over 500 KB. In a git repo `.gitignore` is respected via `git ls-files`; other
paths fall back to a directory walk.

## Architecture

```yaml
storage.py     SQLite manifest (indexed_file, index_run, symbol_reference, scip_*) + sqlite-vec
               vectors, and the overlay a read sees on top of them
service.py     All logic: index_path, search, search_symbol, refresh, get_dependencies, watch
scip.py        Runs scip-python, decodes its protobuf index, and names each reference's caller
watch.py       Filesystem events for `indy watch`: debounced batches and a newest-first queue
chunker.py     Python AST chunking + tree-sitter (Go/TS/Rust/Lua) + prose/code splitters
               + Python reference extraction (call graph)
repos.py       Reads both registries, returns index targets with resolved paths.
               Resolves ownership (owned vs reference) from which registry a repo came from.
config.py      XDG paths, config.toml loading, and constants
main.py        Thin Typer CLI wrappers over service.py
```

## Data

- Index: `$XDG_DATA_HOME/indy/indy.db` (override with `data_dir` or `INDY_DIR`)
- Live overlay: one file per index under `$XDG_CACHE_HOME/indy/overlays/`, written only by
  `indy watch`; `indy config show` prints its path
- Embeddings: ollama `nomic-embed-text` at `http://localhost:11434`

Chunk paths are stored `~`-relative, so one index file is portable across machines
and platforms. The WAL is checkpointed at the end of every index run, so `indy.db`
is a complete copy on its own — safe to back up or replicate without its sidecars.

## License

MIT
