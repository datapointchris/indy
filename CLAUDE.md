# indy

Semantic search index for local codebases, docs, and notes.
Local-first: sqlite-vec + ollama. No external APIs, no separate vector DB process.

**service.py is the API.** All callers go through service.py. Never call storage.py directly from main.py.

## Storage

The database is `$XDG_DATA_HOME/indy/indy.db`. Override the data dir with `data_dir` in
config.toml or the `INDY_DIR` env var.

There is exactly one database, at `DB_PATH`, on every machine — whether this machine built
it or a syncer delivered it. Nothing else is read, and nothing else needs reasoning about.

## The index is replaced, never written

Two invariants make `indy.db` safe for a file syncer to replicate, and every write path
exists to preserve them.

**Nothing ever opens `indy.db` for writing.** A run seeds `indy.db.building` beside it with
`VACUUM INTO`, indexes into that, and renames the finished copy over `indy.db` —
`service.index_session`, over `storage.open_working_db` and `commit_working_db`. One session
spans a whole invocation, not one per target: the swap costs a copy of the database, so per
target would be seventy copies. `clear_errors` goes through the same session; copying the
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
clones kept to be *read* as examples of specific patterns; each entry carries `exemplary_for`
(what it demonstrates) and `index_exclude` (subtrees to keep out of the index). They are
separate files because the two answer different questions and need different fields.

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
still being there — `~/dev/standards` became a symlink and a walk-based prune took the whole
of the fleet's standards out of the index on the next run. Pruning on disk absence instead
means a file newly covered by an exclude pattern keeps its rows until it is really deleted,
which leaves stale content rather than losing live content. The prune is also skipped
entirely when the walk finds nothing, since an unmounted root makes every path missing at
once. `indy forget` clears a label that really did go.

Active repos from `repos_registry`, exemplar clones from `exemplar_registry`, plus any
`[[extra_paths]]` in config.toml. All three default to nothing outside indy's own XDG dirs —
a default that names a particular machine's layout is the bug this arrangement exists to
prevent, and it is why no directory of personal notes appears anywhere in this repo.

Chunk paths are stored `~`-relative (`compact_path`), so one index file is portable across
machines and platforms — which is what makes "index on one machine, search from all of them"
work when `data_dir` points at a replicated tree.

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
  `indy config` prints the source next to the value.
