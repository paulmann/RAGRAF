# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added (publication pass, 2026-10-16)
- Root `install.ps1` — deployment installer: fetches the package from `github.com/paulmann/RAGRAF` (or deploys from a local copy with `-SourceDir`), asks for the target directory (default `C:\RAGRAF`), delegates the virtual environment and dependencies to `scripts/install.ps1`, offers to locate AutoClaw and DeepSeek Harness and to configure the connection, and finishes with admin-panel instructions and an access warning. An existing installation is never overwritten silently, `-SkipDeps` runs a rehearsal, exit codes follow the documented 0/1/2/3 contract, and a transcript is written to `~/.ragraf/logs/`.
- `install.ps1` shows AutoClaw values obtained from the product itself (`client_setup.state()`) instead of hardcoding them, and says plainly why it does not write them: AutoClaw accepts MCP servers only through its own interface, and its store belongs to the product. The Harness profile block is written for real — with a backup and a parse check.
- `docs/ru/**` refreshed from the current Russian originals; `README.ru.md` follows the current code.

### Changed
- `src/ragraf/client_setup.py`, `src/ragraf/probes.py`: the hardcoded SagaAI project path is gone; the location comes from `RAGRAF_SAGA_ROOT`, and an unset value is reported as «not found» instead of being guessed.
- `pyproject.toml`: license expression `MIT` → `PolyForm-Noncommercial-1.0.0`, matching `LICENSE` since 2026-10-15.
- `.gitignore`: the whole `/catalog/` directory is ignored — it holds `db_access.json` with a database secret, which the previous per-file rules left uncovered.
- English documentation counts brought back in line with the code (38 facade operations, 40 MCP tools); the tool list and the capability table were regenerated from the sources.

### Added
- English documentation tree (`README.md`, `docs/architecture/`, `docs/implementation/`, `docs/article/`, `docs/skills/`) — full translation of the Russian originals with doc↔code sync notes; Russian originals preserved as `README.ru.md` and `docs/ru/`.
- `ROADMAP.md` with stable improvement IDs (R-*) derived from the 2026-10 production revision.
- `docs/DEFECTS.md` — the full defect register of the revision (43 findings, `file:line` evidence).
- `CONTRIBUTING.md`, `SECURITY.md` (with an honest threat model and known-open items), issue templates (defect report, doc divergence), PR template.
- GitHub Actions CI (`ci.yml`): hygiene (syntax, BOM/CRLF, doctrine), PowerShell parser + PSScriptAnalyzer, install + 6 portable suites + synthetic-corpus smoke on Windows/Ubuntu × Python 3.13/3.14, package build with PEP 639 verification.

### Changed (installer refactor — production pass)
- `scripts/install.ps1`: concurrent stdout/stderr drain (fixes pipe deadlock); per-step timeouts with `-TimeoutScale`; free-disk-space gate (`-MinFreeGb`); Microsoft Store Python stub detection; `selftest`/`index` parsed from `--json` instead of localized text; transcript log to `~/.ragraf/logs/`; `-SummaryJson` for CI; guarded `-Force` venv removal; «venv inside repo» warning for new venvs; usage-vs-environment exit codes fixed; cross-platform activation hints. **(Defects D-01…D-12.)**
- `scripts/install-joern.ps1`: opt-in `-Download` (official GitHub release + Adoptium API) with streaming download, size sanity and **SHA-256 verification before unpacking**; disk-space gate; `JAVA_HOME`/`PATH` restored in `finally`; platform-aware launcher discovery + `--help` smoke test with timeout; PHP-frontend confirmation via `--list-languages`; documented exit codes. **(D-13…D-15.)**
- `scripts/ragraf.ps1`: interpreter resolution (`-Python` → install venv → local `.venv` → PATH), Python ≥3.13 version gate, Store-stub skip, `[IO.Path]::PathSeparator`, `PYTHONIOENCODING` save/restore. **(D-16, F-03.)**
- `scripts/PSScriptAnalyzerSettings.psd1`: shipped analyzer baseline — 0 errors / 0 warnings with four justified rule exclusions.

## [0.1.0] — 2026-10-12

First public release («Публикация RAGRAF: установщик, README, обезличенное дерево», `df7c1be`).

### Added
- Core: 22 facade operations, 23 MCP tools, three SQLite stores (graph / lexvec / control), immutable revisions with an append-only drift ledger, checksum guard with auto-reindex and locking (pid-liveness aware).
- Indexing pipeline: scan → tree-sitter AST (16 languages) → name resolution → schema from migrations → optional Joern CPG → FTS5 + 384-dim vectors (`fastembed`, no torch), embedding reuse by `chunk_sha256`.
- Optional CPG layer: Joern 4.0.99 + JDK 21 integration with 12 run-verified workarounds documented (`docs/03_implementation/CPG_INTEGRATION.md` §8); honest `capability=degraded` mode.
- Entry points: `ragraf` CLI (exit-code contract 0/1/2/3), `tools/ragraf-mcp.py` (MCP stdio server), `tools/ragraf-admin.py` (loopback admin panel), `scripts/ragraf.ps1`.
- Telemetry: `usage_call` journal with per-agent/per-task statistics and savings estimates (estimates labeled).
- Installer with step-by-step verification and summary table; 10 test suites on synthetic fixtures (6 portable, 164 checks).
- Measurement artifacts: M2 rev-0012 (20 commits) and rev-0025 (8 commits) in `bench/results/`.

### Fixed (found during installation checks, before publication)
- `tools/ragraf.py` crashed after `pip install` — the file name shadowed the package (`'ragraf' is not a package`); the wrapper now strips `tools/` from `sys.path`.
- `pyproject.toml`: removed the legacy license classifier — incompatible with `license = "MIT"` under PEP 639 and broke the whole build.
- Added `ijson` to dependencies (needed by the CPG export parser) and the optional `tls` extra for `truststore`.

### Found by
- Installation-verification runs of the maintainer environment (see the release commit message for the full protocol: clean-environment install, synthetic-project init→index→search→symbol→code, 6 portable suites, syntax of 49 files, BOM/CRLF clean).

[Unreleased]: https://github.com/paulmann/RAGRAF/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/paulmann/RAGRAF/releases/tag/v0.1.0
