# indy

Semantic search index for local codebases, docs, and notes.
Local-first: sqlite-vec + ollama. No external APIs, no separate vector DB process.

**service.py is the API.** All callers go through service.py. Never call storage.py directly from main.py.

## Storage

The database is `$XDG_DATA_HOME/indy/indy.db`. Override the data dir with `data_dir` in
config.toml or the `INDY_DIR` env var.

A read sees two stores. The base is `DB_PATH`, on every machine, whether this machine built
it or a syncer delivered it. The overlay holds each save made since the base was built, and
exists only where `indy watch` has run. It is a file under `$XDG_CACHE_HOME/indy/overlays/`
named from `DB_PATH`'s resolved path, so each index has its own, and `INDY_DIR` or
`data_dir` moves the overlay along with the index.

**For any file the overlay holds, as rows or as a tombstone, the base's rows are hidden.**
`get_read_db` attaches the overlay and creates temp views named `chunk`, `indexed_file` and
`symbol_reference`, and `scip_document` and `scip_occurrence` where the base has them. SQLite resolves an unqualified name in the temp schema first, so every
query in `storage.py` reads the merged store without naming either one. Overlay ids are
negated in the views, which keeps an id unique across both. Vector search is the exception:
a vec0 MATCH needs the virtual table itself, so `search_chunks` runs KNN on each store and
merges by distance. A machine with no overlay attaches nothing and reads the base as before.

**The overlay is written in place, and that is safe only because nothing syncs it.** It
lives in the cache directory, never under `data_dir`, which is the directory a syncer
replicates. Everything below about never writing `indy.db` is about a file under a syncer,
and the overlay is never under one.

**An overlay applies only over the base it was reconciled with.** It records the stamp of
the base it was last compacted against: inode, size and mtime, which every replacement
changes. A read whose base carries another stamp attaches the overlay without laying it
over, and its freshness line says the saves are set aside. That is the state on a machine
whose base a syncer just replaced. Its saves were compared with an index that is gone, so
they could hide newer content. A running `indy watch` sees the new stamp within seconds,
compacts by content hash, and walks every root again.

**A full index takes back what it covers.** After a session swaps in a new base, the
overlay drops every row whose content the base now holds, and every tombstone whose path
the run pruned. Under the labels the session itself indexed, a row also drops when the base
read its file at the same or a later mtime. Both mtimes then come from this machine's
files. Anywhere else the base's mtime may come from another machine's copy, so the match is
by content hash alone. Linked worktree rows stay, because no full index walks a worktree.
When `indy watch` holds the overlay past its busy timeout, the run leaves the overlay to the
watcher and still succeeds. The watcher compacts against the new stamp anyway, and failing
would report an index that is already in place as a failed run.

## The index is replaced, never written

Two invariants make `indy.db` safe for a file syncer to replicate, and every write path
exists to preserve them.

**Nothing ever opens `indy.db` for writing.** A run seeds `indy.db.building` beside it with
`VACUUM INTO`, indexes into that, and renames the finished copy over `indy.db` —
`service.index_session`, over `storage.open_working_db` and `commit_working_db`. One session
spans a whole invocation, not one per target: the swap costs a copy of the database, so a
session per target would copy it once for every target a full run covers rather than once.
`clear_errors` goes through the same session; copying the
whole database to delete a few rows is disproportionate, and it is the price of there being
no second write path.

**Searching cannot modify it.** Reads go through `get_read_db`, which opens `mode=ro`. A
normal connection to a WAL database writes to the main file when the last connection closes,
because SQLite runs a passive checkpoint — so a plain `indy search` used to modify the file,
and on the machines whose copy a syncer maintained, every search made that copy diverge from
the one it had been sent.

Together these give the syncer a file that is never half-written and only ever changes whole.
A finished index is in delete journal mode, so it is complete on its own with no sidecar to
lose, and a read leaves it byte-identical.

**Why not write it incrementally.** WAL mode does not hold `indy.db` still: autocheckpoints
rewrite pages throughout a run, so a syncer hashing a multi-gigabyte file over minutes ships
blocks read at different moments and the peer assembles a database that never existed at any
instant. Nothing reports it — SQLite validates nothing on open and only errors on a page it
actually reads, so the damage accumulates unseen. Syncing `indy.db` with only the `-wal` and
`-shm` sidecars excluded ran for months, corrupted the index on every machine, and first said
so twelve days later as `database disk image is malformed`. By then `index_run` was returning
rows of chunk text and no copy on any machine was salvageable.

The costs are deliberate: a run copies the whole database up front, which is noise against a
multi-hour run and disproportionate for one small repo, and results appear all at once at the
end rather than incrementally. In exchange, a crashed run cannot damage the index it was
built from.

**An interrupt swaps in what the run managed.** Ctrl-C is how a long index gets stopped, and
every file is committed as it is embedded, so the working copy is a complete database at that
moment. Discarding it lost hours of work and left no trace the run happened at all — the
`index_run` rows live in the working copy too — so the next run correctly found nothing
indexed and offered to re-embed every file.

## Two Registries

`repos_registry` holds the portfolio — repos we work in. `exemplar_registry` holds third-party
clones kept to be *read* as examples of specific patterns. indy reads `name`, `path`, `owner`
and an optional `index_exclude` (subtrees to keep out of the index) from each exemplar entry,
and ignores any other field. They are separate files because the two answer different
questions and need different fields.

**Exemplars are indexed under a qualified `owner/name`.** Bare names collide across the two
registries, because a clone commonly shares its name with a repo you own, and a collision puts
that name in both the owned and reference sets so `--owned` returns a stranger's code.

Search supports `--owned` / `--reference` to filter. Default is to search everything.

## What Gets Indexed

A file that disappears between runs is pruned by the next index of its target, with its
chunks and vectors. That has to happen during a run: indexing only ever visits files that
exist, so nothing else revisits the path that stopped existing.

**Gone from disk, not missing from the walk.** The two read as the same test and are not.
`Path.walk` does not follow a symlink, so anything behind one is absent from the walk while
still being there — a directory moved elsewhere and replaced by a symlink to it would drop out
of the index whole on the next run under a walk-based prune. Pruning on disk absence instead
means a file newly covered by an exclude pattern keeps its rows until it is really deleted,
which leaves stale content rather than losing live content. The prune is also skipped
entirely when the walk finds nothing, since an unmounted root makes every path missing at
once. `indy forget` clears a label that really did go.

**`indy watch` covers every target but the exemplar clones**, which change only when pulled.
It also covers each owned repo's linked worktrees, found with `git worktree list` rather
than by any directory convention. A worktree file indexes under the repo's label and carries
its branch, and only while it differs from the main checkout's copy. An identical copy would
return every hit twice. Either copy can change which side of that line it is on, so a save
in the main checkout re-checks the same file in each of its worktrees. A removed worktree's
rows drop when git stops listing it.

Active repos from `repos_registry`, exemplar clones from `exemplar_registry`, plus any
`[[extra_paths]]` in config.toml. All three default to nothing outside indy's own XDG dirs —
a default that names a particular machine's layout is the bug this arrangement exists to
prevent, and it is why no directory of personal notes appears anywhere in this repo.

Chunk paths are stored `~`-relative (`compact_path`), so one index file is portable across
machines and platforms — which is what makes "index on one machine, search from all of them"
work when `data_dir` points at a replicated tree.

An exemplar's `index_exclude` patterns apply on top, using gitignore semantics: patterns in order,
leading `!` re-includes, last match wins. This is what a global skip-list cannot express — FastAPI
ships its docs in every language they are translated into, so `docs/*/**` then `!docs/en/**`
keeps the English pages and drops every translation of them. This prints how much the patterns
cut across the exemplar clones:

```bash
uv run python - <<'EOF'
from indy.repos import load_exemplar_repos
from indy.service import collect_indexable_files

targets = load_exemplar_repos()
walked = sum(len(collect_indexable_files(t.path)) for t in targets)
kept = sum(len(collect_indexable_files(t.path, t.exclude)) for t in targets)
print(f'index_exclude drops {walked - kept} of {walked} exemplar files')
EOF
```

## Key Decisions

- **Cosine distance in vec0** — nomic-embed-text embeddings are not unit-normalized; cosine gives
  correct [0,1] similarity scores. L2 would produce incorrect rankings for non-normalized vectors.
- **KNN oversample + Python filter** — vec0 MATCH can't filter on metadata; oversample 20× then
  filter by repo/language in Python.
- **No git post-commit hook** — `indy watch` sees every save, committed or not, and a
  scheduled `indy index` rebuilds the base. Per-repo hook management adds maintenance
  overhead and would still miss uncommitted work.
- **A save re-embeds only the chunks whose text is new** — every other chunk reuses the
  vector already stored for that text, from the overlay, the base, or a worktree's main
  checkout. An embedding depends only on its text and the model, so editing one function
  embeds one chunk rather than the whole file.
- **symbol_reference is Python-only, and syntactic** — tree-sitter already parses Go/TS/Rust/Lua
  for chunking; reference extraction for those languages is deferred. Beside `call`, `import` and
  `inherit` rows, a name used as any other value is a `reference` row. That is the only trace a
  function placed in a dispatch table leaves, since nothing calls it by name.
- **`deps A.b` resolves receivers without a type checker** — it matches a module hint of `A`,
  which includes a parameter annotated with `A` itself (labeled `call`); `self.b` in `A`, its
  ancestors, or a subclass that does not override `b`; `.b` on a parameter annotated with one of
  those other classes (`typed`); and a bare `b` in a file named `A`. A subclass that defines `b`
  is cut off with everything below it, because its override is a different method. Every caller
  carries the rule that matched it as `via`. A call of `.b` on an untyped receiver is a
  `candidate`, listed after every resolved caller and counted apart from them in the header.
  Matching a method by name alone buries the real caller among every unrelated `.install` in the
  index. Exact resolution and inferred types are a type checker's job, so these rules leave
  them to scip-python.
- **scip-python resolves owned Python repos, inside the full index and never in `indy watch`.**
  It runs from the repo's `.venv` with an `--environment` file listing its packages. A uv
  venv has no `pip`, and without that file the run aborts with an empty index. A repo with
  no venv is skipped, because without the project installed scip-python names its modules
  after their paths (`src.pkg.mod`). A machine without Node.js skips every repo. A skipped
  or failed repo keeps no SCIP rows, and `scip_index` records why. Exemplars are left out. A
  run on a large repo takes tens of seconds, which no save can wait for.
  `indy status --json | jq '.scip[] | {repo, seconds}'` prints each repo's last run time.
- **scip-python is pinned with its whole dependency tree, by the lockfile in
  `src/indy/scip_python/`.** `npm ci --ignore-scripts` installs it once per lockfile under
  the cache directory, and `npx --prefix --no` runs that copy. `npx --yes pkg@version` would
  pin only the top package. The rest of its tree would resolve from caret ranges on each new
  machine, and that tree runs over every owned repo and its venv.
- **An interrupted index leaves the repo skipped, with no SCIP rows.** The session still
  swaps in the files it indexed, so the previous run's rows would describe content that is
  gone, and the overlay compaction that follows would unhide them.
- **In a repo scip-python ran over, `deps` takes callers from it.** A caller the syntactic
  rules also reach keeps their label. A call to a method the target overrides is
  `override`, from SCIP's implementation links. Otherwise a name read off a receiver no rule
  types is `inferred`, and a bare name is `call` or `reference`, since an import or a scope
  reaches it rather than a type. A syntactic candidate there survives only where SCIP
  resolved nothing of that name in the same caller. Every other file keeps the syntactic
  rules: other repos, worktrees, and any file the overlay holds, whose SCIP rows the views
  hide because they describe old content.
- **A SCIP reference takes its caller from `ReferenceExtractor`'s scopes.** SCIP gives an
  enclosing range only to definitions, and none to module-level code. Reusing the scopes
  the syntactic rows are named from gives both tiers one caller name, `STEPS['wslconfig']`
  included. scip-python reports every use as a read: an import, an annotation, a class
  base, a `global`, and every assignment or `del` after a name's first. The extractor's
  non-value spans and store positions drop all of them. A call inside an annotation runs,
  so its arguments are values again, as `f` in `Annotated[bool, Option(callback=f)]` is.
- **An occurrence must spell its symbol's name, or an import alias of it.** scip-python
  resolves a PEP 695 type parameter to the function declaring it, and writes a class's type
  parameter as a second definition of the class. Only references to a symbol an indexed
  file defines are kept, which also drops the standard-library modules it attributes to the
  project's own package.
- **The SCIP index is decoded from the protobuf wire format.** indy reads only the fields its
  field-number constants name, so it decodes the wire format itself rather than take on the
  `protobuf` runtime and a module generated by one protoc version.
- **A repo's scip-python run is skipped while its fingerprint is unchanged.** It covers the
  Python files' content hashes, the installed packages, the lockfile's hash,
  `EXTRACTOR_VERSION`, and `SCIP_LINK_VERSION`. That last sits beside `link_documents` in
  `scip.py` and is raised whenever the rows it writes change.
- **An attribute read through `self`, `cls` or an annotated parameter is a `reference` row** —
  a property read and a bound method passed as a callback have no other trace. A read through
  any other local is skipped, since nothing says what class the local holds.
- **Changing the extractor bumps `EXTRACTOR_VERSION`** — an unchanged file is skipped before it
  is embedded, so a new extractor would never reach it. A file whose stored version differs has
  its references re-extracted from the content the scan already read, keeping its vectors. The
  version is per file rather than per index, because `indy index <repo>` covers one target and
  a global version would mark every other target current.
- **Ownership resolved at service layer, not storage** — service.py resolves `--owned`/`--reference`
  into a set of repo names and passes it to storage as a generic filter. Storage knows nothing
  about ownership.
- **Every read states the index's horizon, and states the oldest scan in scope** — a miss is
  otherwise the same sentence whether a symbol does not exist or was written after the last
  scan. The newest scan describes only the last repo someone indexed, so quoting it calls a read
  current when it is most likely out of date. Dated from `index_run`, not
  `indexed_file.indexed_at`, which moves only when a file's content changes.
- **Age, and no count of files changed since** — counting them needs a gitignore-aware walk of
  every target on a read that must stay cheap, and a stat-only approximation cannot see a *new*
  file, so it would report zero for exactly the case the freshness line exists to catch.
- **Settings resolve env var → config.toml → default, and report which layer won** — an index that
  searches nothing is almost always a config file that was never read rather than a wrong path, so
  `indy config show` prints the source next to the value.
