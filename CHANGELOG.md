# CHANGELOG


## v1.1.0 (2026-09-02)

### Chores

- Sync the generated configs to toolchain 18
  ([`5bbecfe`](https://github.com/datapointchris/indy/commit/5bbecfe27ed3c7b3c693ae586076188966a8879b))

Both stamped files come from the fleet's version declaration: the pre-commit config and the
  generated workflow. Nothing here is a repo decision.

Stamp 18 carries the refcheck hook at v0.6.0, a codespell exclude widened to go.mod, and — on a
  private repo — runs-on naming the self-hosted pool with the actionlint config that declares the
  label.

- Sync the generated configs to toolchain 19
  ([`d75893f`](https://github.com/datapointchris/indy/commit/d75893f8efff4a83e688d3df3d4fb526d70001f8))

Both stamped files come from the fleet's version declaration: the pre-commit config and the
  generated workflow. Nothing here is a repo decision.

Stamp 19 passes --allow-parallel-runners to golangci-lint. A repo with two Go components runs two
  Lint jobs at once, and on a single self-hosted box the second one dies on the shared cache lock
  before linting anything.

- **precommit**: Drop the commit-branding hook
  ([`c89989f`](https://github.com/datapointchris/indy/commit/c89989ff85ba8d21afd8ef587f901303c25348c9))

Claude Code suppresses its own commit and PR attribution through its attribution setting, which
  resolves an empty string to no trailer at all. A hook that strips the trailer afterwards has
  nothing left to remove.

- **pyproject**: Raise assertion verbosity instead of test verbosity
  ([`d0f3e2a`](https://github.com/datapointchris/indy/commit/d0f3e2a672356cfe98c2b1ead6a6663288d1e4f3))

A failing assertion truncated its diff and printed "use -vv to show", so the reader re-ran the whole
  suite to see it. addopts = "-vv" answered that by raising test-list verbosity as well, which is a
  different question: a green run printed a line per test and said nothing. verbosity_assertions
  raises only the half that was wanted.

Written by the forge pyproject die.

### Continuous Integration

- Regenerate validate.yml at toolchain 16
  ([`29349fc`](https://github.com/datapointchris/indy/commit/29349fc80cc9540945966aaf061cd40281d5d5fb))

Catches this repo up with the version manifest: StyLua pinned to a release rather than latest, a
  reworded bats discovery note, and double quotes in the node block. Only the blocks this repo
  declares are affected.

Triggers and job structure are unchanged.

### Documentation

- Drop the table count, which was three and is four
  ([`c9c0e2f`](https://github.com/datapointchris/indy/commit/c9c0e2f822a5b82cb1bea2f28beb37c6a1266a84))

indexed_file, index_run, chunk and symbol_reference, plus the vec_chunks virtual table. The yaml
  block directly beneath already listed all five.

### Features

- **reads**: State how far behind the index is on every read
  ([`2b4a9d1`](https://github.com/datapointchris/indy/commit/2b4a9d1fec3c96998c46d11917389118d46cad3c))

A miss reads as an absence. `No symbol named 'Boundary' found` is the same sentence whether the
  symbol does not exist or was written after the last scan, and indexing is manual, so the second is
  routine — a symbol committed twenty minutes ago is missing, and a symbol that moved file still
  answers from where it was. The tool was accurate up to a boundary and silent about where the
  boundary was.

search, symbol and deps now close with the age of the index over the repos the read could reach,
  printed on a hit as well as a miss, and `--json` carries the same thing as a freshness object.
  status reports it too.

The number is the oldest scan in scope, not the newest. Indexing one repo makes the newest scan
  minutes old while every other label stays where it was, so a read quoting the newest calls itself
  current at exactly the moment it is most likely to be answering out of date. Dated from index_run:
  a file whose content hash is unchanged is skipped and keeps its old indexed_at, so the manifest
  dates the last change to a repo rather than the last look at it.

Age alone, with no count of files changed since. Counting them needs a walk of every target with
  gitignore semantics, which is a subprocess per repo on a read that has to stay cheap, and a
  stat-only approximation cannot see a new file — it would report zero changes for the case the line
  exists to catch.


## v1.0.1 (2026-08-13)

### Bug Fixes

- **config**: Refuse a key indy does not read
  ([`6aaba75`](https://github.com/datapointchris/indy/commit/6aaba753f338dbfc5789107fe9788f13b3a81818))

config.toml parsed into a dict and every reader called .get on it, so a key indy no longer reads was
  dropped in silence and the resolver fell through to a default. That is the shape a rename hides
  in.

Measured 2026-08-13 on this fleet: exemplar_repos_file sat in the deployed config after the key
  became exemplar_registry. indy read the file, dropped the key, resolved its own config directory
  and found nothing there — which reads exactly like a machine that never declared an exemplar
  registry.

Absent stays fine. A machine keeping everything where indy expects it should not have to hold a file
  saying so, and erroring there would break exactly that machine. Present and wrong is now an error,
  and the two were the same answer until this.

The key set is declared rather than derived from the resolve_* calls, because those run at import —
  a list built from them could not be checked before the first one had already answered. A test
  asserts the guard fails closed, so a real key left out of it cannot pass unnoticed.


## v1.0.0 (2026-08-13)

### Documentation

- **config**: Scope the prefix rule to paths indy resolves
  ([`1e58600`](https://github.com/datapointchris/indy/commit/1e5860047b435d7e8c2dc6f4b1d4ae1209ece5ef))

The module docstring said indy reads no variable without an INDY_ prefix. OLLAMA_HOST and
  OLLAMA_MODEL are counterexamples sitting eleven lines below it.

They stay. Those are ollama's own published names, so reading them is interoperating with a service
  rather than accepting a path from a layer indy cannot see, which is what the rule exists to stop.

### Features

- Resolve registries in three rungs, all INDY-prefixed
  ([`92a1945`](https://github.com/datapointchris/indy/commit/92a1945afa64c4a1a3e5d94f6cf38cf8b4b51cf0))

indy read REPOS_JSON between the config key and its default. That variable is set in ~/.env, which a
  process sourcing no profile never reads, so the rung was empty in exactly the unattended runs it
  existed to serve. config.toml is already the machine layer and reaches every process, so the rung
  is gone rather than moved.

The remaining names say who owns them. A tool reading a variable it did not name is how one fleet's
  vocabulary ends up compiled into a generic tool.

BREAKING CHANGE: repos_file is now repos_registry and exemplar_repos_file is exemplar_registry;
  $INDY_REPOS_FILE is $INDY_REPOS_REGISTRY and $INDY_EXEMPLAR_REPOS_FILE is $INDY_EXEMPLAR_REGISTRY.
  $REPOS_JSON is no longer consulted, so a machine relying on it must name the path in config.toml.

### Breaking Changes

- Repos_file is now repos_registry and exemplar_repos_file is exemplar_registry; $INDY_REPOS_FILE is
  $INDY_REPOS_REGISTRY and $INDY_EXEMPLAR_REPOS_FILE is $INDY_EXEMPLAR_REGISTRY. $REPOS_JSON is no
  longer consulted, so a machine relying on it must name the path in config.toml.


## v0.10.0 (2026-08-12)

### Features

- **config**: Resolve $REPOS_JSON below the config key
  ([`ff5985a`](https://github.com/datapointchris/indy/commit/ff5985ab087dd0ddb3c52b70bcc2a980e3599513))

resolve_path gains an optional shared_env for a file indy reads but does not own. The registry is
  read from one place by several tools, so a machine declares the path once instead of each tool
  being pointed at it separately — which is what forced a hand-made symlink from a data directory to
  the real file, reported by nothing.

It sits below repos_file, so naming a different registry for indy alone still works, and above the
  default, so an unset value is the only route to indy's own directory.

The exemplar registry deliberately gets no shared_env: only REPOS_JSON is declared, and a rung
  naming an undeclared variable resolves to nothing and drops silently through to the default.


## v0.9.1 (2026-08-12)

### Bug Fixes

- **index**: Prune on disk absence, not on the walk
  ([`6526ea0`](https://github.com/datapointchris/indy/commit/6526ea0e356502f2184ffd539c8d163c954dea7c))

The prune deleted rows for files the walk did not produce, which is not the same set as files that
  are gone. Path.walk does not follow a symlink, so anything behind one is absent from the walk
  while still being there.

Caught on the first real run: ~/dev/standards became a symlink today, so re-indexing dev pruned 15
  live standards files, and a search for them returned nothing. That is losing live content to clear
  stale content.

Disk absence is the narrow test and cannot do that. The cost is that a file newly covered by an
  exclude pattern keeps its rows until it is really deleted, which leaves stale content instead.


## v0.9.0 (2026-08-12)

### Features

- **index**: Prune files that no longer exist
  ([`bf78190`](https://github.com/datapointchris/indy/commit/bf78190a487ae88e1c3fa4b8d7e128f233146f01))

A file deleted or renamed between runs kept its manifest row, its chunks and its vectors, and no
  later run could clear them: indexing only visits files that exist, so nothing revisits the path
  that stopped existing. Search returned those chunks under a path resolving to nothing. Measured
  before the fix: 119 rows across 7 repos, 74 of them in dotfiles alone.

The walk already knows every path that should be there, so the prune is the set difference against
  the manifest — no new table, contrary to how the item described it.

Scoped to paths under the walked root rather than to the whole label, because names a target after
  its directory and one label can have two roots. Skipped entirely when the walk finds nothing: a
  moved root and a failed git ls-files look exactly like a target whose every file was deleted, and
  pruning on that shape would empty the label. Leaving ghosts is the safe direction, and clears a
  label that really did go.

The count rides on the run result and prints only when it is non-zero, so the line stays quiet on
  the runs where nothing was removed.


## v0.8.0 (2026-08-12)

### Features

- **forget**: Drop a stale repo label from the index
  ([`cd01a26`](https://github.com/datapointchris/indy/commit/cd01a26f964439a3052af67221bb70aa5e6d6d55))

A renamed or absorbed target leaves its old label behind, and re-indexing cannot clear it: a file
  whose content_hash is unchanged is skipped, so it keeps the label it was first indexed under
  however many runs go past. The measured case was ~/notes/dev keeping notes-dev after the target
  widened to notes, which made 'search --repo notes' silently miss 58 files.

Deleting rows goes through index_session like every other write, so the index is still only ever
  replaced whole. vec_chunks carries no foreign key back to chunk, so its rows go by rowid gathered
  before the chunks are deleted, and symbol_reference is keyed by source_file alone, so the manifest
  is read before it is emptied.

index_run rows go too. A label surviving in the run history is one stats still lists, which would
  leave the verb's name a lie.

An unknown label is a usage error ahead of the session, because opening one copies the whole
  database and a typo should not pay for that.


## v0.7.3 (2026-08-12)

### Bug Fixes

- Reject a registry entry with no status
  ([`1c49ad7`](https://github.com/datapointchris/indy/commit/1c49ad75437ff721b77999ee5a8749626cd389c7))

Skipping a status-less entry silently is what let this reader disagree with fleet about which repos
  exist. fleet counted such an entry as active and indy dropped it, so both produced a plausible
  list and nothing could compare them.

status is now required, asserted at commit time in the repo holding the registry. A missing one
  raises and names the offenders rather than quietly shortening the index, which is the failure
  nobody would notice.

standards/data.md, 'A registry field that selects what tools act on is required, never defaulted'.

### Build System

- **precommit**: Resync to forge toolchain 14
  ([`a362e03`](https://github.com/datapointchris/indy/commit/a362e03d3852000748425d1ac539fd4e60ae6d98))

### Documentation

- Cite the standards without a machine path
  ([`e818b86`](https://github.com/datapointchris/indy/commit/e818b867df32e6ca8bc5d810ad3456f3b4653eb5))

The citation carried an absolute path from one machine's layout. What a reader needs is the file and
  the section, and those do not move.


## v0.7.2 (2026-08-08)

### Bug Fixes

- **cli**: Say what a run is doing, not how it does it
  ([`d1e0c9c`](https://github.com/datapointchris/indy/commit/d1e0c9c1d6b5d584c3319bfa90354ebae901b732))

"Copying the index to a working copy — indy.db is never written in place" describes indy's
  implementation to someone watching an index build, which is the leak cli-design.md names: the copy
  and the rename are how the guarantee is kept, not a fact about the index. Now "Snapshotting the
  index (1.4 GB)" and "Saving the updated index", and the stage names behind them match.

The file being worked on is green and fenced from the counts, which ran together into one unreadable
  string. It is escaped on the way out because the corpus holds route files like [slug].md, and rich
  would read the name it is meant to print as markup.


## v0.7.1 (2026-08-08)

### Bug Fixes

- **index**: Keep the work when a run is interrupted
  ([`8c0f330`](https://github.com/datapointchris/indy/commit/8c0f33089a33dcfbaae2238bfdf8f565da57827f))

Building into a working copy meant an interrupted run threw away everything it had done. Ctrl-C is
  how a multi-hour index gets stopped, so this lost whole runs — and left no trace they happened,
  because the index_run rows live in the working copy too. The next run then correctly found nothing
  indexed and offered to re-embed every file, which is how it surfaced.

An interrupt now swaps the working copy in. Every file is committed as it is embedded, so the copy
  is a complete database at the moment it stops. A failure to open or commit still discards, leaving
  the index as it was.

The CLI says what was kept rather than only that it stopped, and exits 130.


## v0.7.0 (2026-08-08)

### Features

- **cli**: Complete repo names and suggest near misses
  ([`2495534`](https://github.com/datapointchris/indy/commit/24955344ba53004644fdf452bb99d4b37a83a01a))

`indy index <TAB>` now offers every name the command accepts, labelled owned or exemplar. Exemplar
  clones are qualified owner/name, so the trailing segment completes too — `fastapi` offers
  `fastapi/fastapi`, since the owner is the part nobody remembers.

An unrecognised name says which names are close instead of only that this one is wrong, and exits 2
  rather than 1: it is a usage error, which cli-design.md gives its own code.

The argument help named repos.json, which is the plumbing leak that standard warns about — it
  describes where the implementer reads names from, and is wrong besides, since exemplar names come
  from the other registry.


## v0.6.0 (2026-08-08)

### Features

- **index**: Count progress against the work, not the file list
  ([`b6045ee`](https://github.com/datapointchris/indy/commit/b6045eedd4d07e201a1709256f80cab5672ac17c))

A target now hashes every file before embedding any of them, so it knows how many need re-embedding
  and can say so. The bar tracks that count rather than the walk: previously it raced through
  skipped files and crawled through changed ones, which on a re-index meant it filled while nothing
  happened.

Two lines per target, matching the two passes:

fastapi 455/2871 scanned behind-a-proxy.md fastapi 29/2871 updated · 2871 files README.md

The scan costs one extra read of each file that changed, since those are read again to embed.
  Against the cost of embedding them it does not show.


## v0.5.0 (2026-08-08)

### Features

- **index**: Show how many files a target actually re-embedded
  ([`4ecd5d8`](https://github.com/datapointchris/indy/commit/4ecd5d8502282d86ebdd50cd884b6e4c0da59e85))

The progress line counted scanned against total, which on a re-index races to the end while almost
  nothing happens — every file whose content hash is unchanged is skipped without being embedded.
  The number that says whether a run is doing work was only ever visible in the summary printed
  afterwards.

Targets now read `40/220 12 updated`, and the counter is padded so it stops jittering as it gains a
  digit. on_progress takes an IndexProgress record rather than three positional arguments, and fires
  once more when a target finishes so its closing tick reports the last file rather than stopping
  one short of it.


## v0.4.1 (2026-08-08)

### Bug Fixes

- **storage**: Never write the index in place
  ([`f376f80`](https://github.com/datapointchris/indy/commit/f376f800a7a256dbf954968fc231a97ac1aae73f))

Syncing indy.db corrupted it on every machine. WAL mode does not hold the file still:
  autocheckpoints rewrite pages throughout a run, so a syncer hashing 1.4 GB over minutes shipped
  blocks read at different moments and peers assembled a database that never existed at any instant.
  Searching made it worse from the other side — every connection opened read-write, and SQLite
  passive-checkpoints on the last close, so `indy search` on a machine that only reads still
  modified the file it had been sent.

Nothing reported any of it. SQLite validates nothing on open and only errors on a page it actually
  reads, so the damage accumulated for at least twelve days before a re-chunk walked into it. By
  then index_run returned rows of chunk text and no copy on any machine was salvageable.

Two invariants now hold instead. Indexing seeds indy.db.building with VACUUM INTO, builds there, and
  renames the result over indy.db, so nothing ever holds the index open for writing and it only
  changes whole. Reads open mode=ro, so searching cannot modify it. clear_errors goes through the
  same session rather than being a second write path.

The swap also removes the outgoing database's sidecars, before the rename rather than after. A -wal
  is read as the live tail of whatever database sits beside it, so one surviving a swap makes
  searches answer out of the replaced database with no error anywhere — the state every machine
  upgrading from the WAL-mode index is in.

A run now copies the database up front and publishes its results at the end rather than
  incrementally. In exchange a crashed run cannot damage the index it was built from.

### Continuous Integration

- Drop the push trigger the release run already covers
  ([`11a2475`](https://github.com/datapointchris/indy/commit/11a2475cf95113752b51d5d1ae2939624f4dd4f2))

release.yml fires on push to main and calls validate.yml, so emitting push here ran every job twice
  for one commit. workflow_call still covers main; only the duplicate goes away.

Generated by forge v5.1.0, which derives this from release.yml rather than a flag, so it cannot
  drift back.


## v0.4.0 (2026-08-07)

### Bug Fixes

- **chunker**: Cap whole-file config chunks by character count
  ([`dfd459e`](https://github.com/datapointchris/indy/commit/dfd459ef48f42b093453ce78c1a93e7f5351b459))

chunk_config gated the whole-file path on line count alone, so a one-line file passed at any size. A
  97KB minified JSON became a single chunk and ollama returned 500 rather than truncating it.

Measured the boundary: nomic-embed-text runs at a 2048-token context, failing at ~11,000 chars of
  prose. The cap is set well under that for dense content, which tokenizes closer to 2 chars per
  token.

Every other chunker already caps its output, so this branch was the only one that could emit an
  oversized chunk.

### Features

- **index**: Show overall target progress across a full run
  ([`7595e0a`](https://github.com/datapointchris/indy/commit/7595e0ada64059b841553c5fcf3f98c7fde7c87d))

Per-target tasks are removed as each finishes, so a bare 'indy index' had nothing on screen spanning
  the run — the bar restarted silently at every target and a long run read as no progress at all.

Add a task over the target list, so the display reads targets completed above files completed. Only
  the no-argument branch needs it; --path and --repo index a single target where the file count is
  the whole story.


## v0.3.1 (2026-08-07)

### Bug Fixes

- **update**: Drop the private-repo token workaround
  ([`fe1bd8d`](https://github.com/datapointchris/indy/commit/fe1bd8d892870ccc7cb41e539029fc7511a4dfc3))

The repository is public, so the release lookup no longer 404s without credentials and pyselfupdate
  finds releases on its own. Removing the `gh auth token` subprocess means self-update works on any
  machine rather than only where the gh CLI is logged in.

Verified with GITHUB_TOKEN and GH_TOKEN unset.


## v0.3.0 (2026-08-07)

### Chores

- Add the MIT license file
  ([`f8fda12`](https://github.com/datapointchris/indy/commit/f8fda12be50332b0cee9401718d222afcacc7fc6))

pyproject.toml has declared MIT since the first release, but no LICENSE file shipped, so the terms
  were only visible to someone reading the packaging metadata.

- **lint**: Disable SC1091/SC1090 from the forge toolchain
  ([`ae92204`](https://github.com/datapointchris/indy/commit/ae92204f28d8215567711d1bb3e04c0bec146401))

### Features

- **config**: Resolve settings from config.toml
  ([`e772d7f`](https://github.com/datapointchris/indy/commit/e772d7f8bb6f703c36c8192757244c6312e1dd39))

Defaults named a particular machine: the DB in ~/dev/indy, the registries in ~/dev, and ~/obsession,
  ~/notes and ~/dev as extra index paths. A comment in config.py went further and recorded which
  personal directory holds recovery codes, to explain why it was excluded.

Every setting now resolves environment variable -> $XDG_CONFIG_HOME/indy/ config.toml -> a default
  under indy's own XDG dirs, so nothing in the repo knows about any directory layout. The index
  defaults to $XDG_DATA_HOME/indy; data_dir points it at a replicated tree for setups that index on
  one machine and search from all of them, which is what the reverted 351dd74 could not express when
  the location was a constant.

Add `indy config`, which prints each resolved value beside the layer that supplied it. An index that
  searches nothing is nearly always a config file that was never read rather than a wrong path, and
  that is invisible otherwise.

Checkpoint the WAL (PRAGMA wal_checkpoint(TRUNCATE)) at the end of every index run. WAL mode leaves
  the newest commits in indy.db-wal, so anything copying indy.db alone - a backup, or a sync peer
  replicating to a machine that only searches - otherwise lands on the state before the last run.


## v0.2.0 (2026-08-05)

### Features

- **cli**: Answer --version, like the rest of the fleet
  ([`ede1266`](https://github.com/datapointchris/indy/commit/ede1266df1a2e218c08f8810d81ef745e949da03))

indy was one of the CLIs that could not say which build was running, even though the hard half was
  already here: `get_installed_commit_hash` was written, tested by nothing and called by nothing. It
  is wired to an eager root option now, printing `indy <version>` with the commit appended when uv
  installed from a git ref.

The rule and its reasoning are in `~/dev/standards/cli-design.md` § "`--version` on every CLI".

Also the first CLI test in this repo, covering the two things a caller shelling out to indy relies
  on: the version line, and help plus exit 2 on a bare invocation.


## v0.1.4 (2026-08-04)

### Bug Fixes

- Report the update in the verb that ran it
  ([`479e837`](https://github.com/datapointchris/indy/commit/479e83755466c4a44745541ac7d97e47d29ba0fc))

pyselfupdate 0.2.2 says "updated" and "update failed" where it used to say "upgraded" and "upgrade
  failed". The command is `update`; one command, one vocabulary.

### Chores

- **toolchain**: Adopt the generated configs and CI
  ([`eb832c9`](https://github.com/datapointchris/indy/commit/eb832c9b2627aca603ac2c66579448d525590d0b))

Brings the repo onto forge toolchain manifest 11.

bandit, refurb and pyupgrade drop out: pyupgrade is ruff's UP rules, already selected, and the other
  two are the manifest's deliberate narrowing to the rule set every repo actually runs.

### Documentation

- Flush dormant markdownlint violations
  ([`d0f21eb`](https://github.com/datapointchris/indy/commit/d0f21eb4096df11f13b33c8389db1e02c20a7a60))

markdownlint only runs on the files a commit touches, so unmodified docs accumulate violations
  invisibly. The toolchain sync bumps markdownlint to v0.47, which added MD060, and runs --all-files
  — surfacing every one of them at once, in the middle of an unrelated change.

Table separators are normalized to the compact `| --- |` style MD060 expects, which --fix cannot
  repair; everything else is markdownlint --fix.


## v0.1.3 (2026-07-31)

### Bug Fixes

- **ci**: Run ruff and pytest without depending on repo dev deps
  ([`e45803a`](https://github.com/datapointchris/indy/commit/e45803afffdd920475bce3a9b6c7ee809e1d5baf))

`uv run ruff` resolved ruff from the repo's own dependencies, so a repo that treats ruff as a fleet
  tool rather than a project dependency failed to spawn the binary instead of linting. ruff now runs
  through uvx at the version its pre-commit hook pins; pytest is supplied with --with so a real test
  suite is never silently skipped; and mypy's guard tests for the dependency by import, since the
  [tool.mypy] section it used to look for is now in every repo.

Regenerated by `forge dies run maintenance/sync-ci.sh`.


## v0.1.2 (2026-07-31)

### Bug Fixes

- **ci**: Validate on push, not only via a release call
  ([`054f5db`](https://github.com/datapointchris/indy/commit/054f5db8131cb9aaea5608ce89e8e56efcb9d314))

The workflow triggered on pull_request and workflow_call. Development here is trunk-based, so the
  only trigger that ever fired was this repo's release pipeline calling it, and the checks ran as
  part of a release rather than as a gate on the push itself.

### Chores

- **config**: Record the keys the pyproject sync owns
  ([`13a2030`](https://github.com/datapointchris/indy/commit/13a20309381bb8d61c51565a76d4d253da606810))

forge now writes [tool.forge] managed, listing the exact keys the standard sets. Deletion on a later
  sync is scoped to that record, so dropping a key from the template retracts it here without having
  to guess which settings belong to this project.

Purely additive: nothing else in this file changed.


## v0.1.1 (2026-07-31)

### Bug Fixes

- **chunker**: Fall back when tree-sitter is missing
  ([`6e8a066`](https://github.com/datapointchris/indy/commit/6e8a066431b82f387a3b8ae132752757c86cc398))

The except ImportError branch left _Parser unbound while setting a flag that claimed to cover it. A
  bool cannot narrow a name, so chunk_treesitter passed its own guard with the grammars absent and
  raised NameError instead of falling back to chunk_code.

An empty _TS_LANGUAGES is the entire fallback on its own -- every lookup misses and returns
  chunk_code -- so the flag went with it, and Parser is imported where a table hit already proves
  the package loaded.

### Chores

- **config**: Adopt the standard pyright section
  ([`658d10c`](https://github.com/datapointchris/indy/commit/658d10c4864da7420689c43916abc075c8f800fb))

Synced from forge's pyproject template via sync-pyproject. basedpyright defaults to typeCheckingMode
  "recommended", which enables its own strict rules; repos were answering that one rule at a time.
  "standard" turns the whole family off at once.

The rules only take effect from a config file: pyright discards LSP client settings entirely once a
  project config exists, so the nvim-side ignore never applied here.

### Continuous Integration

- Add generated validate.yml and gate release on it
  ([`f646eef`](https://github.com/datapointchris/indy/commit/f646eeffabbd77c7616b11e1d55315083456c21c))

Release triggered on push to main with no validation at all, so it published whatever was on main.
  Adds the forge-generated CI block (ruff check, ruff format, mypy, pytest) and makes release depend
  on it.

Verified locally before wiring the gate: all four checks pass.

- Regenerate validate.yml at toolchain 6
  ([`552467d`](https://github.com/datapointchris/indy/commit/552467d7a40e03d266f265533c9e504a8f3acd47))

Stamp only — the python block is unchanged. Toolchain 6 adds the pinned release-binary mechanism and
  the shell CI block.


## v0.1.0 (2026-07-27)

### Bug Fixes

- Connect zero-value points along baseline in streamline chart
  ([`b901398`](https://github.com/datapointchris/indy/commit/b90139877901200a766960dcb51c96dd7263dc78))

Zero values got height 0, which falls below the chart's renderable range (rows 1–height), creating
  gaps in the line. All values now map to at least height 1 (the baseline) so the line is
  continuous.

- Disable Rich auto-highlighting in CLI output
  ([`b4f6bb7`](https://github.com/datapointchris/indy/commit/b4f6bb77837863e1a5a530bb79345b080617b56e))

Rich's default highlighter colors numbers, URLs, etc. automatically, causing partial coloring of
  values like '1.1s'. Disable it so only explicit markup is applied.

- Filter stale error files by path existence in status
  ([`0f603b3`](https://github.com/datapointchris/indy/commit/0f603b3980489f795b761bc7bc339e9598a6d7ef))

Error rows from other machines (different home dir paths) persist in the shared DB and never get
  cleared by re-indexing. Filter them out in get_status so only locally relevant errors are shown.

- Migrate to Ollama /api/embed endpoint
  ([`1a3233a`](https://github.com/datapointchris/indy/commit/1a3233a9fefd44270ffe18bc530b33c30d3dc292))

Ollama v0.20+ removed the legacy /api/embeddings endpoint. Update embed_text() to use /api/embed
  with the new request/response format (input/embeddings instead of prompt/embedding).

- Move default data dir to XDG_DATA_HOME
  ([`273c0c1`](https://github.com/datapointchris/indy/commit/273c0c1a9d6df08cf29f2d5bd6c8329bbd50cf2f))

The default INDY_DIR was ~/dev/indy/, but ~/dev is Syncthing-synced. File-syncing a live WAL-mode
  SQLite DB does not work: the -wal/-shm sidecars are created and destroyed mid-write, so peers
  announce then fail to pull them, the 1.2 GB DB retransmits on every write, and concurrent writers
  produce sync-conflict copies of the database.

The index is derived per-machine state, so it belongs in $XDG_DATA_HOME/indy/. REPOS_FILE stays in
  ~/dev — it is input config, not derived state, and is worth syncing.

- Remove dim styling from CLI output
  ([`4643a1e`](https://github.com/datapointchris/indy/commit/4643a1e4c6143bdf52f0a68f59ce0ff008941080))

Dim text is unreadable on many terminal themes. Replace all [dim] markup with plain text or existing
  color styles.

- Remove duplicate .planning gitignore entry
  ([`0380d4e`](https://github.com/datapointchris/indy/commit/0380d4ed3ac2c451832c6b9637e4b1c66b08a9a8))

- Revert embedding model to nomic-embed-text
  ([`96bfca1`](https://github.com/datapointchris/indy/commit/96bfca13f9c3a160da4fbffcdd7f2cd1158105a8))

nomic-embed-code does not exist on Ollama's registry — only in Nomic's direct API/SDK. Reverts
  b9c7ed9 to restore a working default.

- Streamline chart label truncation and remove area fill
  ([`4be68ff`](https://github.com/datapointchris/indy/commit/4be68ffa55c864e4a2cde431e0bd03aa1b443ad7))

Labels were truncated to 3 chars (lbl[:3]), making all same-month dates display identically (e.g.
  "04-" for every April date). Column width now adapts to label length so full MM-DD labels render
  correctly.

Also converts streamline from area chart to line chart by removing the block fill below the curve,
  giving a lighter visual weight.

### Chores

- Add .planning to gitignore
  ([`cb0cd4b`](https://github.com/datapointchris/indy/commit/cb0cd4b836c88d2d52bb1471a0e041044cad924e))

- Add pre-commit config and markdownlint config via forge sync-pre-commit
  ([`aff9d3b`](https://github.com/datapointchris/indy/commit/aff9d3b48fb7f0fdf47aabe58ba6e5562b9307b3))

- **mcp**: Remove MCP server in favor of the CLI
  ([`6c19039`](https://github.com/datapointchris/indy/commit/6c19039682856c3ad2fc211b97ccf84d27b634cc))

Follows the nomad/learning conversions: agents now shell out to the indy CLI (indy search --json,
  indy symbol, indy deps) instead of calling MCP tools. The MCP server was a second thin consumer of
  service.py with no capability the CLI lacks after the parity commit.

Removed: - src/indy/mcp/ (FastMCP server + __main__ entrypoint) - indy-mcp script and mcp[cli]
  dependency from pyproject - indy mcp help command - service.get_file_content — dead once
  indy_get_file is gone; the CLI reads files via cat/Read rather than the store - MCP references in
  README, CLAUDE.md, and service docstring

### Documentation

- Add CLAUDE.md with architecture, storage, and key decisions
  ([`8083eab`](https://github.com/datapointchris/indy/commit/8083eaba32cdabb882ea6dd9f0f443ae1f8feb01))

- Document gitignore support and clear-errors cmd
  ([`8407f55`](https://github.com/datapointchris/indy/commit/8407f550242e3b44bec8b3d23c08f119bf5300cf))

Update CLAUDE.md with the new .gitignore filtering behavior and the clear-errors CLI command.

- Update CLAUDE.md with new CLI commands
  ([`706b764`](https://github.com/datapointchris/indy/commit/706b764e47ad39ae008b933b30fab25bcfdb5d22))

Add errors, errors-clear, stats commands and charts.py to the architecture and CLI sections.

### Features

- Add clear-errors command
  ([`6c65b63`](https://github.com/datapointchris/indy/commit/6c65b63a4d917af7a067ae5314830a1afbc3aabf))

Adds `indy clear-errors` to remove all error records from the indexed_file table. Cleared files will
  be re-attempted on the next index run.

- Add errors, errors-clear, and stats commands
  ([`278f435`](https://github.com/datapointchris/indy/commit/278f43541440a3edd657f0378901d9a233208f8e))

- `indy errors`: per-file error listing grouped by repo - `indy errors-clear`: renamed from
  clear-errors for grouping - `indy stats`: horizontal bar charts for files/chunks per repo, sorted
  by size, plus last-indexed dates - `indy stats {repo}`: vertical bar charts showing scan history -
  `indy status` simplified: shows last 5 runs instead of batch aggregation, error count with hint
  instead of inline details - Improved embed_text error capture to include Ollama response body -
  New charts.py module with Unicode block-character rendering

- Add indy-mcp script entrypoint for installed tool usage
  ([`1d5b795`](https://github.com/datapointchris/indy/commit/1d5b79505056100bc4986b4901706b2547b437fc))

Adds indy-mcp to project.scripts so the MCP server works when installed via uv tool install, without
  needing a dev checkout. Also bumps uv_build upper bound to <0.12.

- Add progress bar to index command
  ([`63657a5`](https://github.com/datapointchris/indy/commit/63657a5f316b6e517064ffe369df00959312a6e1))

Show a Rich progress bar during indexing with file count and current filename. Uses an optional
  callback on index_path/index_repo/index_all so the service layer stays UI-agnostic.

- Add releases, and adopt pyselfupdate for update and notify
  ([`5cc458d`](https://github.com/datapointchris/indy/commit/5cc458d452feae1b875d455581f54dc159d51af7))

indy had no release process at all — no tags, no workflow — so its update command reinstalled from
  main HEAD with no ref, tracking a branch rather than a release, and parsed a commit hash out of
  uv's stderr with a regex to tell whether anything had changed. Adds python-semantic-release on the
  same config the other Python tools use, which is what gives the update command something to
  resolve.

`fetch_github_changes` was the third verbatim copy of the same function, after syncer and relate.
  That is what made this worth a library.

The repository is private, so the release lookup 404s without a token. That comes from `gh auth
  token` via token_func rather than token, because the config is built at import and the notify gate
  resolves it on every invocation: an eager subprocess would be paid by every indy command.

The notice stays silent until the first release lands — a check that cannot find one records the
  reason and swallows it.

- Add repo ownership filtering for owned vs reference repos
  ([`da49ade`](https://github.com/datapointchris/indy/commit/da49ade5ab630a1b5e9453c7aec8a18767085e10))

repos.json per-repo `owner` field distinguishes owned repos from reference repos (third-party code
  cloned for study). Search accepts --owned/--reference flags (CLI) and owned parameter (MCP).

Ownership resolved at service layer into repo name sets; storage layer stays generic with a repos
  inclusion filter.

- Add self-update command
  ([`ab8e477`](https://github.com/datapointchris/indy/commit/ab8e477678cd754754212051448ff4cb808cd0eb))

Runs `uv tool install --reinstall` from GitHub, shows current/new commit hash using PEP 610
  direct_url.json metadata.

- Add streamline area charts with box-drawing curves
  ([`e47b97f`](https://github.com/datapointchris/indy/commit/e47b97f1a7e89134c7ec0b180e3fdc4fbc0041b2))

Replace vertical_bars with streamline() for repo scan history in `indy stats {repo}`. Uses Unicode
  box-drawing characters (╭╮╯╰│─) for smooth transitions between data points, with solid fill below
  the line. Horizontal block-character bars remain for size comparisons.

- Add symbol_reference call graph (Phase 3)
  ([`00878f5`](https://github.com/datapointchris/indy/commit/00878f54fd2241741731f58c8eff324bcda7f16c))

Adds Python AST reference extraction during indexing, populating a new symbol_reference table with
  import/call/inherit edges. Enables callers and callees lookup without a separate graph database.

- chunker.py: Reference dataclass + _ReferenceExtractor NodeVisitor + extract_references() —
  captures source_symbol context via a name stack so every call is attributed to the correct
  function/method - storage.py: symbol_reference table with indexes on source_file and
  target_symbol; delete_file_references, insert_references, get_symbol_callers, get_symbol_callees -
  service.py: wires reference extraction into index_path alongside chunk deletion; adds
  get_dependencies(symbol_name, repo, direction) - mcp/server.py: indy_get_dependencies tool
  (callers/callees/both)

- Add tree-sitter AST chunking for Go, TypeScript, Rust
  ([`6533f79`](https://github.com/datapointchris/indy/commit/6533f7991b8ef6e89f64bb66f644471e2ea8da5a))

Add optional tree-sitter chunking that extracts function/method-level chunks for .go, .ts, .tsx, and
  .rs files with correct symbol names and types. Falls back to recursive code splitter if packages
  are absent.

Node types: function_declaration + method_declaration (Go), function_declaration + method_definition
  (TS/TSX), function_item (Rust). Recursion stops at matched nodes to avoid double-chunking nested
  defs.

- Add ~/notes/dev as configured non-repo index path
  ([`7ef3517`](https://github.com/datapointchris/indy/commit/7ef35171167911b095264074496f8dd2a6d9b229))

EXTRA_PATHS_RAW in config.py defaults to notes-dev (~~/notes/dev). load_extra_paths() in repos.py
  resolves and filters by existence. index_all() now indexes repos.json repos + extra paths in one
  pass. Override with INDY_EXTRA_PATHS env var (JSON array).

- Implement phase 1 — indexing, search, MCP tools
  ([`c10d144`](https://github.com/datapointchris/indy/commit/c10d144802e4178ddc793905f6a32d40e5d93194))

- storage.py: SQLite manifest schema + sqlite-vec vector table (cosine distance), chunk CRUD,
  index_run audit trail - repos.py: load active repos from ~/dev/repos.json - chunker.py: Python AST
  chunking (function/class/method), recursive code splitter, paragraph-aware prose splitter, config
  whole-file - service.py: index_path, index_repo, index_all, search, search_symbol, refresh; Ollama
  embeddings via httpx - Wire CLI commands (index, search, status, repos) to service.py - Wire all 6
  MCP tools to service.py - Use cosine distance_metric in vec0 for nomic-embed-text compatibility -
  Update README with usage, architecture, and data location docs

- Improve CLI output for status and index commands
  ([`249d852`](https://github.com/datapointchris/indy/commit/249d852567d1976d8baab9897b984fa61134cca0))

Status: group error files by repo and message instead of listing each file individually. Detect
  interrupted runs (NULL finished_at) instead of showing None values.

Index: show elapsed time after every index operation.

- Improve status error reporting
  ([`60eb216`](https://github.com/datapointchris/indy/commit/60eb21681f8dda7e799f2824678aafc9848b2c2a))

- get_error_files() surfaces which files failed and their error messages - get_index_stats() adds
  repo_count and files_scanned to last_run - status command lists each errored file with repo, path,
  and truncated error message rather than showing only a count

- Index the personal-knowledge dirs by default
  ([`852933d`](https://github.com/datapointchris/indy/commit/852933d3fbaa0ac3aa1f1024c701ccf5e28e2b25))

EXTRA_PATHS_RAW only covered ~/notes/dev, so ~/obsession, ~/notes proper, and ~/dev were absent from
  the index entirely. The decision reasoning that no repo records was therefore unfindable via
  search, while third-party exemplar clones made up ~60% of the corpus.

~/shart stays out: it holds recovery codes and key material in .txt files that DOC_EXTENSIONS would
  pull in.

Also document that indexing runs on Arch only — the Intel Macs are too slow for a full run, and
  indy.db lives in Syncthing-synced ~/dev with ~-relative chunk paths, so one machine indexes and
  all machines search.

- Initial project scaffold
  ([`d979661`](https://github.com/datapointchris/indy/commit/d979661cb0afe847da6ca5ffa11ca96baaecf22f))

Skeleton Python package with CLI (typer), MCP server stubs (FastMCP), and config. All commands and
  tools return "not yet implemented" — structure is in place for Phase 1 implementation.

- Normalize DB paths with ~ for cross-machine portability
  ([`b5fb69c`](https://github.com/datapointchris/indy/commit/b5fb69cd765f1ed30406d717c1bf121374f6957d))

Store file paths as ~/... in the database so a single shared DB works across macOS (/Users/chris)
  and Arch (/home/chris). Compact paths on write in index_path, expand back to absolute on read in
  search/status/dependencies/get_file_content.

Reverts the path-existence filter from 0f603b3 which was hiding errors instead of fixing the root
  cause.

- Respect .gitignore when indexing repos
  ([`8ff920f`](https://github.com/datapointchris/indy/commit/8ff920f73cb2e05c4e6ffd072099ed76fcc08c72))

Use `git ls-files --cached --others --exclude-standard` to collect files in git repos, skipping
  gitignored files like .coverage.json and build artifacts. Falls back to os.walk for non-git paths.

Also consolidates the two-pass walk (count + process) into a single collect-then-iterate pattern for
  the progress bar.

- Split exemplar repos into their own registry
  ([`0fa2067`](https://github.com/datapointchris/indy/commit/0fa2067b4aa4c3686b97ff767aa7d5bc21f022bc))

Exemplar clones lived in repos.json marked only by an `owner` field, which caused two problems.

Names collided. Both ~/homelab and khuedoan's clone are called `homelab`, so 3427 chunks carried one
  label for two repos, and that name landed in *both* the owned and reference sets — `--owned`
  returned a stranger's infrastructure. Exemplars are now indexed as `owner/name`, which cannot
  collide.

The index was mostly noise. 80% of chunks came from repos we don't own, and a third of that was
  translations and test fixtures: FastAPI alone shipped 1501 translated copies of its 154 English
  doc pages. A global SKIP_DIRS set cannot express "this repo's docs are 91% duplicates", so each
  exemplar now carries its own `index_exclude` patterns with gitignore semantics — ordered, `!`
  re-includes, last match wins. Across the corpus this drops 33% of files; FastAPI drops 75%.

The registries stay separate because they answer different questions: an exemplar needs
  `exemplary_for` and `index_exclude`, neither of which belongs on a repo we actually work in.

- Switch embedding model to nomic-embed-code
  ([`b9c7ed9`](https://github.com/datapointchris/indy/commit/b9c7ed9707a0a9b252e7ceee3f6638c3f383382e))

nomic-embed-code is trained on code+docs pairs and better suited for code search than the
  general-purpose nomic-embed-text. Same 768-dim output, same cosine distance setup — no schema
  changes needed.

Re-index required: rm ~/dev/indy/indy.db && ollama pull nomic-embed-code && indy index

- Switch to sqlite-vec + ollama, move DB to ~/dev/indy/
  ([`2671e9c`](https://github.com/datapointchris/indy/commit/2671e9cf019d18c08f8e16c14ea47719f41c4bb7))

Replace chromadb (incompatible with Intel Mac onnxruntime) with sqlite-vec for vector storage. Use
  ollama via httpx for embeddings — no external API dependency. DB path moved from ~/shart/indy/ to
  ~/dev/indy/ since the code index is cross-cutting dev infrastructure, not personal document
  storage.

- **cli**: Add symbol and deps commands with --json output
  ([`2e5719f`](https://github.com/datapointchris/indy/commit/2e5719fb04c31ebf82c5e64e8b70d499a132c5e9))

Reach full CLI parity with the MCP tools ahead of retiring the server: - indy symbol <name> → exact
  symbol lookup (was indy_search_symbol) - indy deps <symbol> → reference-graph callers/callees (was
  indy_get_dependencies) - --json flag on search, status, repos, symbol, deps emits the same JSON
  shapes the MCP tools returned, for agent/scripting consumption

refresh and get_file need no new command: indy index already does the incremental re-index refresh
  performed, and file contents are read straight from disk (cat/Read) rather than the store.

### Refactoring

- Make index positional arg a repo name, consistent with stats
  ([`c0aea56`](https://github.com/datapointchris/indy/commit/c0aea56be7391ee4c49b0470225a531d2bc99467))

Previously `indy index indy` treated the arg as a path (resolving to the data directory), silently
  indexing 0 files while writing a junk index_run. Now both `index` and `stats` treat the positional
  arg as a repo name from repos.json. Arbitrary paths move to `--path`.

Also adds proper error messages for unknown repo names in both commands. Removes unused `--repo` and
  `--all` flags from index.

- Remove underscore prefix from all module-level functions
  ([`d493c98`](https://github.com/datapointchris/indy/commit/d493c98b7eb29dc82ae26284138a783aba5b676c))

Renames 16 functions across 5 files to drop the _ convention. content_hash renamed to
  compute_content_hash to avoid shadowing the local variable of the same name in index_path.

- Use pathlib instead of os for filesystem ops
  ([`1e5634d`](https://github.com/datapointchris/indy/commit/1e5634d379629f28788f6653773b11710b7a1320))

- Replace os.walk with Path.walk() (Python 3.12+) in service.py - Replace manual ~ expansion with
  Path.expanduser() in repos.py - Replace open()/json.load with Path.read_text()/json.loads in
  repos.py - Remove os import from service.py (config.py retains it for os.environ)

- **update**: Unify upgrade output and show changelog
  ([`39e6395`](https://github.com/datapointchris/indy/commit/39e6395d20b2362ede5f345900173a75342b3e22))

Drop the "Current version" preamble and route all outcomes through consistent status glyphs: `✓ indy
  already at latest: <hash>`, `✓ indy upgraded: <before> → <after>`, `✗ indy upgrade failed:
  <reason>`. After a successful upgrade, fetch commit subjects between the old and new commits from
  GitHub's compare API and print them under "Changes:".

- **update**: Use httpx for GitHub compare call
  ([`f237ee5`](https://github.com/datapointchris/indy/commit/f237ee510ddc225a6a8f9fe8107c7ea8438626c6))

Replace urllib.request with httpx.get for consistency with the rest of the project (httpx is already
  a dep) and to avoid B310 bandit warnings on tools that lint for it.
