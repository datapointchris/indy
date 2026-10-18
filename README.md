# indy

Semantic search across the code, docs and notes on your own disk. You ask in words, and it returns
the chunks that do what you described, from every indexed repo at once. Two exact reads sit beside
the semantic one. `symbol` finds a definition by name. `deps` finds what calls it.

Nothing leaves the machine. ollama embeds the text, and sqlite-vec keeps the vectors in one SQLite
file. There is no external API and no vector database process to run.

ripgrep finds a string you already know. A language server resolves one project's references
exactly. indy finds code by what it does, across every repo you index. It answers from an index as
old as its last scan, and every lookup says how old that is.

## Install

indy installs as a uv tool, from the package index every release is published to. It needs Python
3.13 or later.

```bash
uv tool install --index https://pypi.ichrisbirch.com/simple/ indy   # the newest release
indy --version                                                       # the version, and the commit it was built from
indy update --check                                                  # whether a newer release exists
```

Name the index on the install: without it uv asks PyPI, where `indy` is a different project. uv
records the index beside the tool, which is where `indy update` reads it back.

`indy update` reinstalls the newest release over the current one. It refuses an install from a
local checkout or from a branch, because neither carries a release version to compare. Every other
command checks for a newer release at most once a day, and says so in one line after its own output.

ollama is installed separately, from [ollama.com](https://ollama.com). Exact Python callers need
Node.js with `npm` and `npx`. Without them, indy runs and `deps` answers by syntax alone.

## Starting from scratch

```bash
ollama pull nomic-embed-text           # the embedding model; ollama must be serving on localhost:11434
indy config show                       # every path indy reads, the layer that named it, and which are (missing)
$EDITOR ~/.config/indy/repos.json      # the repos to index, at the repos_registry path config show printed
indy index                             # embeds every active repo, exemplar and extra path; Ctrl-C keeps what finished
indy status                            # totals, the run just made, and scip-python's result per repo
indy search "where retries back off"   # proves it worked: ranked chunks, closed by how old the index is
```

indy runs on defaults alone, and the defaults index nothing. Every registry and extra path is yours
to name, and the shape of each is under Configuration. `indy index --path ~/writing` indexes one
directory under its own name, with no config at all.

**When a search returns nothing, run `indy config show` first.** An index that searches nothing is
almost always a config file that was never read, not a wrong path. `config show` prints each
setting beside the layer that supplied it. A stale environment variable in one shell shows up there
rather than as an empty result.

The first index embeds every file, and across many repos that takes hours. Each file is committed
as it is embedded. Ctrl-C saves everything embedded so far as the index, and exits 130. The next
run embeds only the files that are new or whose content changed.

Until a first index is saved, `status`, `search`, `symbol` and `deps` exit 1 with
``No index at <path>. Run `indy index`, or wait for a peer to deliver one.`` The second half is for
a machine that receives its index from a synced directory rather than building one.

## The grammar

Commands are flat verbs, with `config show` the one pair. Most take a label. A label names one
indexed root:

- a repo you own, by its registry `name`
- an exemplar clone, as `owner/name`
- an extra path, by the `name` its config entry gives it
- a directory indexed with `--path`, by the directory's own name

`index`, `stats` and `forget` take a label as their argument. `search`, `symbol` and `deps` take one
as `--repo`. TAB completes a label for `index`, and matches an exemplar on its name alone. `index`
answers a bare exemplar name with its `owner/name`. It answers a directory typed as a label with the
label that already covers it. Indexing that tree by `--path` would put it in the index twice, under
two labels.

`--json` is on every read a script would pipe: `search`, `symbol`, `deps`, `status`, `repos` and
`config show`.

## Which surface answers which question

| Ask | Surface | Reads |
| --- | --- | --- |
| Where is the code that does this | `search` | the vectors, after ollama embeds the query |
| Where is this function or class defined | `symbol` | the chunks, by exact name |
| What calls this, and what does it call | `deps` | Python references, from scip-python where it ran |
| Keep results current while I edit | `watch` | filesystem events under every repo, worktree and extra path |
| Bring the index up to date | `index` | the registries, the extra paths, and every file under them |
| How old is the index, and did the last run work | `status` | the run history, scip-python's results, and the error count |
| Which files failed to index, and why | `errors` | the per-file error records |
| What is in the index, and how big | `repos`, `stats` | file and chunk counts per label, and one label's scan history |
| Which settings are in effect | `config show` | the environment, `config.toml`, and the defaults |
| A repo was renamed or removed | `forget` | the one label's rows |

## `search` and `symbol` — every answer closes on how old the index is

```bash
indy search "parse a config file and report the layer"   # the ten closest chunks, from everything
indy search "retry with backoff" --owned --language go     # only your own repos, only Go
indy symbol get_read_db                                    # where a function or class is defined, by exact name
```

`search`, `symbol` and `deps` each close with how far behind the index is. `--json` carries the same
thing as a `freshness` object. A full index runs only when someone runs it. So a symbol written
today is absent from an index scanned last week. A flat "no symbol named X" cannot be told apart
from a symbol that does not exist.

The line gives the range of scan ages across every label the read could have matched. The older
end is the one that bounds the answer. Indexing one repo makes the newest scan minutes old, while
every other label stays where it was.

Where `indy watch` runs, the line also counts the files saved since the scan, and says when the
watcher last wrote. Where the index was replaced after the watcher last compared its saves with it,
the line says those saves are set aside.

`--owned` reads only the repos registry, and `--reference` only the exemplar registry. With neither,
a search covers everything, extra paths included. A result from a linked worktree carries
`@branch`.

## `deps` — callers by syntax everywhere, by type checker where scip-python ran

```bash
indy deps get_read_db               # callers and callees of a bare name
indy deps Store.save -d callers     # Owner.member: the callers of one method, each with how it matched
```

`deps` reads Python only. Each caller carries a label saying how it matched:

- `call`, a call by the name, or for `Owner.member` a call through the owner's name or through a
  parameter annotated with the owner
- `reference`, the same match with the name used as a value, as a function placed in a dispatch
  table is
- `self`, `self.member` inside the owner, a class it inherits from, or a subclass that does not
  override the member
- `typed`, `.member` on a parameter annotated with one of those other classes
- `module`, a bare `member` in a file named after the owner that does not import it
- `candidate`, `.member` on a receiver nothing types

Candidates come after every resolved caller, and are counted apart from them. Matching a method by
name alone buries the real caller among every unrelated method of that name in the index.

`indy index` also runs [scip-python](https://github.com/sourcegraph/scip-python), Pyright's indexer,
over each Python repo in your own registry. Where it ran, `deps` takes that repo's callers from the
type checker. Two more labels appear there. `inferred` is a call on a receiver only a type checker
types, as a local assigned from a call. `override` is a call to a method this one overrides. A call
by method name that scip-python resolves to another class is not listed.

The lockfile in `src/indy/scip_python/` pins scip-python and everything it depends on. `npm ci`
installs that once, under `$XDG_CACHE_HOME/indy/scip-python/`. scip-python runs from the repo's
`.venv`, with the project installed there. A repo without one is skipped, since scip-python cannot
name the project's modules without it. Without Node.js, every repo is skipped.

A repo whose run fails keeps none of its earlier results, and `deps` answers for it by the syntactic
rules. So does a repo whose index was stopped before the run finished. `indy index` prints what
happened per repo. `indy status` lists every repo it skipped or failed on, with the reason. A repo
is run again only when something the run depends on changes: one of its Python files, its installed
packages, the pinned scip-python, or the code in indy that reads the result.

`indy watch` never runs it, because a run takes about twenty seconds on a large repo. A file saved
since the last index answers by the syntactic rules until the next one.

## `index` — the index is replaced whole, never written in place

```bash
indy index                    # every active repo, exemplar clone and extra path
indy index myrepo             # one label
indy index --path ~/writing   # a directory no registry names, labeled after the directory
indy errors                   # the files that failed, grouped by label
indy forget old-name          # drop a label, so its files index under the current one
```

A run never opens `indy.db` for writing. It copies the index to `indy.db.building` beside it,
indexes into the copy, and renames the finished copy over `indy.db`. The result is in SQLite's
delete journal mode, with no `-wal` or `-shm` file beside it. A read opens the index read-only and
leaves it byte-identical. So a file syncer only ever sees `indy.db` change whole, and never sees it
half-written. A crashed run loses only its copy.

The costs are deliberate. Each run copies the whole database before it starts. Results appear when
the run ends, not as each file is embedded. Ctrl-C is the one early exit, and it swaps in what the
run finished.

A file deleted from disk is pruned by the next index of its label, with its chunks and vectors. The
test is absence from disk, not absence from the walk. A walk does not follow a symlink, so pruning
what it missed would delete live files behind one. The cost is that a file newly covered by an
exclude pattern keeps its rows until it is really deleted. A walk that finds nothing prunes nothing,
since an unmounted root makes every path missing at once. `indy forget` clears a label that really
did go.

A file that fails is recorded with its error, and every later run tries it again. `indy errors`
lists the failures, and `indy errors-clear` drops the records.

## `watch` — each save is searchable a second later, from an overlay nothing syncs

```bash
indy watch         # runs in the foreground until stopped, for a service manager to keep alive
indy config show   # the overlay row is the path of this index's overlay
```

Each save goes into a local overlay that every read on this machine sees on top of the index.
`search`, `symbol` and `deps` return it about a second later. A deleted file drops out of results
the same way.

It watches every active repo in your own registry, each of its linked worktrees, and every extra
path. Exemplar clones are left to `indy index`, since they change only when pulled. Worktrees come
from `git worktree list`, not from any directory convention. A worktree file is indexed under its
repo's label, only while it differs from the main checkout's copy. An identical copy would return
every hit twice. A result from a worktree shows `@branch`.

A save re-embeds only the chunks whose text is new. Every other chunk reuses the vector already
stored for that text. Editing one function embeds one chunk, not the whole file.

Each index has its own overlay, under `$XDG_CACHE_HOME/indy/overlays/`, named from the index's path.
It sits outside the data directory, so a setup that syncs the index never carries it. The overlay is
written in place, which is safe only because nothing syncs it.

An overlay applies only over the index it was reconciled with. `indy index` clears from the overlay
whatever its new index holds. An index that arrives from another machine is reconciled by
`indy watch` within seconds, and the watcher then walks every root again. Until that happens, a read
sets the overlay aside and says so, rather than letting older saves hide the newer index.

## Configuration — environment, then `config.toml`, then a default, and `config show` names which

indy runs on defaults alone. To point it at a repo registry or extra directories, create
`$XDG_CONFIG_HOME/indy/config.toml` (usually `~/.config/indy/config.toml`):

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
name = "writing"
path = "~/writing"
```

Every setting resolves as **environment variable → `config.toml` → built-in default**. The
environment variables are `INDY_DIR`, `INDY_REPOS_REGISTRY`, `INDY_EXEMPLAR_REGISTRY`,
`INDY_EXTRA_PATHS`, `OLLAMA_HOST` and `OLLAMA_MODEL`. `INDY_EXTRA_PATHS` is a JSON list of
`{"name": ..., "path": ...}` objects. `indy config show` prints what each one resolved to and where
it came from.

A key indy does not read stops every command, naming the key and the keys it knows. A renamed key
dropped in silence would leave its setting on the default, with nothing to say so.

The vector table is sized for nomic-embed-text's output, by `EMBEDDING_DIM` in `src/indy/config.py`.
Another model has to embed at the same width.

### Registries — one for the repos you own, one for the clones you read

`repos_registry` lists the repos you own. `exemplar_registry` lists third-party clones kept to be
read as examples. They are separate files because an exemplar carries fields a worked-in repo has
no business having.

```json
{
  "repos": [
    { "name": "myrepo", "path": "~/code/myrepo", "status": "active" }
  ]
}
```

Every entry in `repos_registry` needs `status`, and only `active` is indexed. A missing `status`
stops the run and names the entry, rather than leaving the repo out without a word.

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

An exemplar is indexed under `owner/name`. A clone often shares its bare name with a repo you own.
Under one shared label, `--owned` would return a stranger's code.

`index_exclude` keeps subtrees of an exemplar out of the index, with gitignore semantics. Patterns
apply in order, a leading `!` re-includes, and the last match wins. That ordering is what a global
skip-list cannot express. FastAPI ships its docs in every language they are translated into, so
`docs/*/**` then `!docs/en/**` indexes the English pages and drops the near-duplicates of them.

An entry in either registry whose path does not exist on this machine is skipped, as is an extra
path that does not.

## What gets indexed — code, prose and config, found the way git finds them

Code in Python, Go, Rust, Ruby, Lua, shell and bats, JavaScript and TypeScript, Vue, Astro, HTML,
CSS and SCSS, Terraform and HCL, SQL, and `Dockerfile`. Prose in `.md`, `.rst` and `.txt`. Config in
`.yaml`, `.yml`, `.toml` and `.json`. The exact suffixes and names are `CODE_EXTENSIONS`,
`CODE_FILENAMES`, `DOC_EXTENSIONS` and `CONFIG_EXTENSIONS` in `src/indy/config.py`.

A file with no suffix is indexed when its first line is a shebang running `sh`, `bash`, `zsh`,
Python, or `uv run`. Only that line is read to decide.

In a git repo, the files are the ones `git ls-files` lists, tracked and untracked, so `.gitignore`
is respected. Other paths fall back to a directory walk. Either way, these are always skipped:

- the directories `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`,
  `.mypy_cache`, `.ruff_cache` and `.pytest_cache`
- the lock files `uv.lock`, `package-lock.json`, `yarn.lock`, `Cargo.lock`, `go.sum` and
  `.terraform.lock.hcl`
- any file over 500 KB

## Where it writes — one portable file, and a cache nothing syncs

- The index: `$XDG_DATA_HOME/indy/indy.db`, moved by `data_dir` or `INDY_DIR`. A run builds
  `indy.db.building` beside it.
- The live overlay: one file per index under `$XDG_CACHE_HOME/indy/overlays/`, created only where
  `indy watch` runs. `indy config show` prints its path.
- scip-python: one install per lockfile under `$XDG_CACHE_HOME/indy/scip-python/`.

Chunk paths are stored `~`-relative, so one index file is portable across machines and platforms.
Pointing `data_dir` at a synced directory lets one machine index and every other search. A machine
that only searches never writes the file it receives.

## How the code is laid out — `service.py` is the API

```text
main.py        Thin Typer CLI over service.py
service.py     All logic: index_path, search, search_symbol, refresh, get_dependencies, watch
storage.py     SQLite manifest (indexed_file, index_run, symbol_reference, scip_*) and sqlite-vec
               vectors, and the overlay a read sees on top of them
chunker.py     Python AST chunking, tree-sitter (Go/TS/Rust/Lua), prose and code splitters,
               and Python reference extraction
scip.py        Runs scip-python, decodes its protobuf index, and names each reference's caller
watch.py       Filesystem events for `indy watch`: debounced batches and a newest-first queue
repos.py       Reads both registries and the extra paths into index targets, each marked owned,
               exemplar or extra
config.py      XDG paths, config.toml loading, and constants
charts.py      Terminal bar charts for `indy stats`
```

Every caller goes through `service.py`. `main.py` never calls into `storage.py` directly.

## License

MIT
