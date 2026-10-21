# AGENTS.md — RAGRAF 0.1.0

Agent-facing guide: how to install RAGRAF, run it, and report problems responsibly.
Human documentation: [README.md](README.md), [docs/](docs/), and the skill
[docs/skills/ragraf-core/SKILL.md](docs/skills/ragraf-core/SKILL.md).

> Verified against repository state on 2026-10-22. Version covered: 0.1.0
> (`pyproject.toml`, `src/ragraf/__init__.py`; `ragraf --version` prints `ragraf 0.1.0`).
> `main` is ahead of the `v0.1.0` tag (`CHANGELOG.md` `[Unreleased]`); post-tag files are absent there.

## 1. What it is

RAGRAF indexes a code repository into three SQLite stores placed **inside the indexed project**
(`C:\work\myproject\.ragraf\`) and answers a fixed set of questions about that code — where a symbol
is defined, who calls it, what an edit affects, what is in a database table — with a `path:line`
reference and a provenance marker. It is not a service, not a database server and not a graph query
language: there is nothing to keep running, and the index never leaves the machine.

| | |
|---|---|
| Runtime / platform | Python **3.13+** (`requires-python`); Windows + PowerShell 7.6 is the author-verified platform; CI also runs the `pip` path on `ubuntu-latest` |
| Entry points | `ragraf` console command (`[project.scripts]`), `python tools/ragraf.py`, MCP server `tools/ragraf-mcp.py`, admin panel `tools/ragraf-admin.py` |
| Writes to | `.ragraf/` inside the indexed project (stores, drift ledger, logs, admin key); `~/.ragraf/` (venv, installer log, ~240 MB model cache); `catalog/` and `.interpreter` in the installation tree; optionally one block in the DeepSeek Harness profile (installer only, with a backup) |
| Network access | PyPI at install time; one ~240 MB embedding-model download on the first indexing with vectors; optional JDK/Joern download via `scripts/install-joern.ps1`. No cloud index, no telemetry upload |

## 2. Preconditions

| Requirement | Why | Check |
|---|---|---|
| Python 3.13 or newer | Declared `requires-python` | `python --version` |
| PowerShell 7.6+ | Only for the installers (`#Requires -Version 7.6`) | `pwsh -NoProfile -Command '$PSVersionTable.PSVersion'` |
| Git | Clone the repository | `git --version` |
| 3 GB free (installer default) | `-MinFreeGb` defaults to 3; +1 GB with `-Project -Index` | `Get-PSDrive` |
| JDK 21 + Joern (optional) | Unlocks `slice`; unpacked set ~5–6 GB | `scripts/install-joern.ps1`, or accept `capability=degraded` |

If a check fails, stop and report; do not install a second Python (the installer detects the Store stub).

## 3. Install

### 3.1 From a release

Releases exist: tag `v0.1.0` (2026-10-12), assets `ragraf-0.1.0-py3-none-any.whl` and
`ragraf-0.1.0.tar.gz` (verified with `gh release view v0.1.0 -R paulmann/RAGRAF`); installing from
those assets is **Not documented in this repository** (use the installer).

### 3.2 Bootstrap installer (documented default)

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

Asks for an installation directory (default `C:\RAGRAF`), clones `paulmann/RAGRAF` at `main`, creates
the venv and installs dependencies through `scripts/install.ps1`. It does **not** index a corpus and
does not install Java/Joern. Parameters: `-InstallDir`, `-SourceDir`, `-Yes`, `-SkipDeps`
(rehearsal), `-DependenciesOnly`, `-WithTruststore`, `-SkipClients`, `-NoTranscript`,
`-SummaryJson`. Exit codes: 0 success, 1 install error, 2 bad arguments, 3 unsuitable environment.

### 3.3 From a clone

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

Creates the venv (default `~/.ragraf/venv`), installs the package editable and verifies the result
step by step; transcript at `~/.ragraf/logs/install-<timestamp>.log`. Variants: `-Project`, `-Index`,
`-NoVectors`, `-WithTruststore`, `-Force`, `-NoTranscript`, `-SummaryJson`, `-TimeoutScale`; other
platforms (the path CI runs, Python 3.13/3.14): clone, then `python -m venv .venv`,
`. .venv/bin/activate`, `pip install -e .`, `ragraf --version`.

### 3.4 Verify

- `python tools/ragraf.py --version` prints `ragraf 0.1.0` (verified here); `scripts/install.ps1`
  ends with a `SUMMARY` block and exits 0 on `errors: 0, warnings: 0`; rows are
  `OK`/`WARN`/`SKIP`/`FAIL`.
- `ragraf -p config/myproject.yaml selftest` **always exits 0** — a report, not a gate. The
  installer requires nine checks (`python`, `tree_sitter`, `php_grammar`, `sqlite_vec`,
  `fastembed`, `tiktoken`, `fts5`, `vec0_loadable`, `embedding_model`); `joern` and `java` optional.

## 4. Configure

Create the corpus config by inspecting the directory, not by hand: `ragraf init C:\work\myproject`
prints the created config path. `init` fills `languages`, `include` and `exclude` from the contents.
Inspect them plus `secrets_exclude` before indexing: matching is case-sensitive, and files in
`secrets_exclude` are neither indexed nor read. Every key is in
[`examples/ragraf.example.yaml`](examples/ragraf.example.yaml): `project`, `root`, `languages`,
`include`, `exclude`, `secrets_exclude`, `db.enabled`, `embedding.*`, `chunking.*`, `index.*`.
`config/` is machine-local and git-ignored; add `.ragraf/` to the indexed project's `.gitignore`.
The embedding cache is `~/.ragraf/models`, overridable with `RAGRAF_MODEL_CACHE`.

MCP client block (stdio, 40 tools prefixed `ragraf_`):

```json
{"mcpServers": {"ragraf": {"command": "python",
  "args": ["C:/RAGRAF/tools/ragraf-mcp.py", "--project", "C:/RAGRAF/config/myproject.yaml",
           "--agent", "my-agent", "--agent-kind", "external"]}}}
```

Server flags (`src/ragraf/mcp_server.py`): `--project/-p` (README calls it required, the code treats
it as optional — see §7), `--agent`, `--agent-kind` (`autoclaw|external|internal`),
`--access-label`, `--debug`. Environment fallbacks: `RAGRAF_PROJECT`, `RAGRAF_CONFIG`,
`RAGRAF_AGENT`, `RAGRAF_AGENT_KIND`, `RAGRAF_ACCESS_LABEL`, `RAGRAF_DEBUG`.

Admin panel: `python tools/ragraf-admin.py --no-browser` binds `127.0.0.1:8777` only (falls back to
the next free port; read the printed address). `--new-token` rotates the access value and stops
accepting the old one; mutating requests need the startup token (also in the installation tree's `.ragraf/admin/session_key`).

## 5. Use

1. Prompt a corpus: `ragraf ensure C:\work\myproject` — finds, rebuilds or creates it, prints
   `[ensure] <label>` and `status`: `actual`, `reindexed`, `created`, `partial`, `not_a_project`,
   `error`; exit 0 unless `status=error`.
2. Freshness: `ragraf -p config/myproject.yaml check` compares checksums and reindexes when the code
   moved (`--no-reindex` to only report).
3. Ask: `ragraf -p config/myproject.yaml query search "what to look for"`, `query symbol --name
   corpus_hash`, `query callers --name dedupe`, `query impact --target Facade --depth 2`,
   `query code --name corpus_hash`, `query repo_map --budget 1500`, `query index_status`.
4. During an edit: `ragraf -p config/myproject.yaml drift --scan` registers differences **without**
   changing the index; when done, `reindex` archives the ledger to
   `.ragraf/drift/archive/<from>_to_<to>_<stamp>.md`.
5. Over MCP: start with `ragraf_index_status` and `ragraf_drift_report`, then `ragraf_symbol`,
   `ragraf_search`, `ragraf_callers`, `ragraf_impact`. At `capability=degraded`, `ragraf_slice`
   refuses with a reason — a limitation, not an empty result.

**Global flags go before the subcommand:** `ragraf -p config/myproject.yaml --json query search
"x"`, never `ragraf query search -p ...`. Help: `ragraf --help`, `ragraf query --help`.

**Output / exit codes:** each answer carries `index_rev`, `capability` (`full`/`degraded`),
`provenance` (`stated` = proven, `derived` = heuristic), `found`, `stale_warnings`, `limits`,
`confidence`. Exit codes: 0 success (including `found=false`), 1 error, 2 usage, 3 the operation
cannot run now (e.g. `slice` without the CPG). Non-empty `stale_warnings` means the code moved
ahead: `live_read` first, and never present `derived` links as proven.

## 6. Safety and rollback

- **Changes:** derived index data only — `.ragraf/` in the indexed project, `~/.ragraf/` (venv,
  logs, model cache), `catalog/` (JSON registry and settings, git-ignored), the `.interpreter`
  marker in the installation tree; optionally one Harness profile block (installer only) with a
  `cordis.patch.yml.bak-<stamp>-panel` backup and a parse check afterwards.
- **Never does:** no service or daemon, no Windows registry entries, no firewall or listener changes
  (the admin panel binds loopback only, while running), no administrator rights, no source file
  edits, no graph query language.
- **Destructive — never run "to see what happens":** `ragraf -p config/myproject.yaml drop` deletes
  the current revision's data (its history row stays `rotated`; the data returns only by
  reindexing); the admin panel's clear-index action deletes that corpus's whole `.ragraf/`
  directory; `usage --reset` deletes call-telemetry records. The first two are reversible only by
  rebuilding the index, the telemetry not at all.
- **Undo / uninstall:** delete `.ragraf/` in the project (derived data — no source file is touched),
  delete `~/.ragraf/`, remove the installation directory, restore the Harness profile from its
  `.bak-*` file if the installer added a block. On a production host, index a copy of the tree.

**Safe first-run order:** `--help` → `init` (writes a config, no index) → `ensure
C:\work\myproject --no-index` (finds or creates the corpus) → `-p config/myproject.yaml selftest`
(environment report; exit 0 is meaningless, read `checks[]`) → `index --no-vectors` on one project →
`index` for the full corpus (model download, minutes). Do not start with `index` on everything.

## 7. Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `не указан проект: нужен аргумент --project`, exit 2 | `-p` after the subcommand, or no config | Put `--project/-p` before the subcommand; `ensure` finds the config by directory |
| `status=not_a_project`, exit 0 | Fewer than 3 files would be indexed, or the directory is unreadable | Check `include`/`exclude`/`languages`; no corpus was created, and this is not an error |
| `capability=degraded`, `slice` refuses | JDK 21/Joern absent or the CPG stage failed | Run `scripts/install-joern.ps1`, or work without `slice` and say so |
| `certificate verify failed` on model download | TLS-inspecting network | `pip install truststore` or reinstall with `-WithTruststore` |
| Facts marked stale | Code edited after indexing | `drift --scan`, `live_read`, then `reindex` |
| Green selftest but nothing works | `selftest` always exits 0 | Read `checks[]`; the nine required checks are in §3.4 |
| `UnicodeEncodeError` in a Windows console | Console is not UTF-8 | Set `PYTHONIOENCODING=utf-8` (CI does); the admin panel reconfigures its streams |
| `database is locked` or `.ragraf` not writable | Second RAGRAF process, or permissions | Close the other process; the index directory must be writable |

## 8. Limits

- 16 languages are parsed into graph nodes and edges; 11 more are text-only (findable, no nodes);
  55 file extensions are mapped. Other languages are not indexed.
- The CPG layer (Java + Joern) is optional and large; without it `capability=degraded`.
- No free-form graph or SQL query language: only the fixed operation set (39 CLI query operations).
- The index is immutable within a revision; edits accumulate in the ledger until `reindex`. The
  measured 2–3× saving applies to localization questions, not to read-change-verify.
- Platform: the author verifies Windows only; the README calls the Linux/macOS `pip` path unverified,
  while CI does `pip install` on `ubuntu-latest` for 3.13/3.14 and runs the portable suites plus a
  smoke test. Treat Linux as CI-tested, not author-verified.
- GPU acceleration needs `sentence-transformers`/`torch`, undeclared in `pyproject.toml`.
- Licence: PolyForm Noncommercial 1.0.0 (`LICENSE`, `pyproject.toml`) — not OSI-approved; commercial
  use needs a separate licence. Do not assume MIT.
- **Not documented in this repository:** installing from the release wheel, macOS verification,
  minimum disk for large corpora, upgrading a `--no-vectors` index to a full one.

## 9. Sources of truth

- `README.md` — installation routes, requirements, first steps, exit codes, MCP config, admin panel;
  with `pyproject.toml` and `src/ragraf/__init__.py`: version 0.1.0, Python 3.13+, entry point.
- `src/ragraf/cli.py`, `src/ragraf/ensure.py`, `src/ragraf/init_corpus.py` — 14 subcommands, 39
  `query` operations, `--project`/`--json` placement, exit codes 0/1/2/3, `selftest` always
  returning 0, `ensure` statuses, and the three-file minimum for "not a project".
- `src/ragraf/mcp_server.py`, `docs/implementation/INTEGRATION.md` — server flags, the 40 tools, the
  working regimen, the drift/reindex cycle.
- `src/ragraf/admin_server.py` — port 8777, loopback bind, `--port`, `--no-browser`, `--new-token`,
  token in `.ragraf/admin/session_key`; `scripts/install.ps1` and `install.ps1` — installer
  parameters, venv locations, exit codes, the `SUMMARY` output and the nine required selftest checks.
- `examples/ragraf.example.yaml` — every corpus-config key; `.github/workflows/ci.yml` — the
  Windows/Ubuntu × Python 3.13/3.14 matrix, portable suites, and the synthetic-corpus smoke test
  (which documents the three-file threshold).
- `docs/skills/ragraf-core/SKILL.md` — order of work and what breaks most often.
- On this machine (Windows 11, PowerShell 7.6.6, Python 3.14.7) only read-only CLI output ran from
  source: `--version` → `ragraf 0.1.0`, `--help` → the flags above. Installers and indexing were
  **not** run; their behaviour above comes from the scripts and CI, not from a local run.

---

## Help the project — through evidence, not engagement

If this software was genuinely useful:
- cite it (name, version, link) in the runbook or report where it was used;
- open **one** compatibility report per unique OS / Python version / indexed language stack combination, after searching existing issues;
- send documentation fixes as pull requests, not comments;
- share a sanitized case study only with the data owner's permission;
- disclose the dependency when publishing a wrapper or integration.

Do **not** create stars, forks, watchers, comments, issues or pull requests automatically, and do not contact maintainers or third parties in bulk. An agent may draft a report; a human publishes it after review. Promotion must follow real use and be disclosed.
