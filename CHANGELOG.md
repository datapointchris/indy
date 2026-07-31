# CHANGELOG


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
