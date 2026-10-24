# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-10-24

### Changed (version and delivery, 2026-10-24)
- The version is declared **once**, in `src/ragraf/__init__.py` (`__version__`):
  `pyproject.toml` reads it through `dynamic = ["version"]` and
  `[tool.setuptools.dynamic]`, so the package metadata can no longer drift from the
  code. `CITATION.cff` restates it because CFF requires the field, and the 25th
  doctrine check now asserts the two agree instead of trusting them.
- The shipped tree is rebuilt from the working tree by the pipeline and carries no
  machine-local configuration: the live corpus config was in the build and is out,
  `examples/ragraf.example.yaml` — the documented example — is in.
- `tests/test_db_index.py` no longer carries a live database password. The value comes
  from `RAGRAF_DB_PASSWORD` or from the machine's own access store, which is never
  shipped. Two negative-test placeholder values are declared as placeholders in the
  release checker rather than those files being dropped from the shipment.
- The docs site navigation still names the pre-0.2.0 page paths. The shipped tree uses
  the numbered chapters (`docs/02_architecture/…`), so `mkdocs build --strict` fails
  until the navigation is rewritten; this is stated here rather than fixed silently.

### Changed (licence metadata, 2026-10-24)
- Licence moved from PolyForm Noncommercial 1.0.0 to **Business Source License 1.1**
  (Community). `pyproject.toml` carries the SPDX expression `BUSL-1.1`, and the tree
  ships `LICENSE` (official SPDX text plus the BSL parameters), `NOTICE` (third-party
  components), `CLA.md` (relicensing rights) and `SUPPORT.md`.
- `CITATION.cff`, `mkdocs.yml`, `README.ru.md`, `CONTRIBUTING.md` now point at the
  licence file instead of restating its terms. Restating them is exactly how this tree
  ended up publishing PolyForm terms while `pyproject.toml` said MIT.
- History is **not rewritten**: MIT (until 2026-10-15) → PolyForm Noncommercial 1.0.0
  (2026-10-15) → BUSL-1.1 (2026-10-24). Rights granted under earlier licences are not
  revoked.
- The delivery tree is now built from the working tree by `tools/release/build_release.py`
  against `tools/release/manifest.toml`, checked by `verify_release.py` and gated by
  `gate_release.py`. The wrapper documents in this directory are inputs of that build,
  not a hand-kept copy.

### Added (anonymised polygon measurement, 2026-10-16)
- `README.md`, `README.ru.md` and the wiki's [Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits) now publish the **measured polygon**: **28 registered corpora, 25 indexed** (10 with the Joern CPG layer, 15 degraded) and an anonymised table of the 16 whose revision record was read — **7 527 files, 94 528 symbols, 791 871 edges, 86 790 chunks**. Project names, paths, domains and hashes are withheld: the rows are labelled `C-01`…`C-28` by size, `Kind` is derived from each corpus's file-extension profile, and the private label map is kept outside the repository (in the working tree's git-ignored `.staging/`).
- A **counting discrepancy** was recorded rather than smoothed over: for the *same* revision the corpus registry and the revision record disagree — one corpus reports 8 721 symbols / 41 201 edges against the revision's 4 498 / 56 106, another 13 800 / 45 361 against 3 620 / 46 296. Registered as **E-10** in [`docs/DEFECTS.md`](docs/DEFECTS.md); the published tables quote the **revision record**, because that is what the answer envelope reports, and corpus-level counts taken from the registry are provisional until the two aggregation paths are reconciled.
- The public site (`ragraf.ru`; its source is maintained outside this repository) had its **licence statements corrected** from MIT to PolyForm Noncommercial 1.0.0 in all four locales — page footer, hero eyebrow, FAQ answer and the meta description that carries into search results — and received an absolute `og:image` (1200×630) plus `twitter:card: summary_large_image`. Verified by rendering all four locales locally: `php -l` clean, the site's own i18n gate PASS (809/809 per locale).

### Changed (repository presentation, 2026-10-16)
- `.github/workflows/ci.yml`: the doctrine step no longer comments a fixed baseline («2 known pre-existing errors»); it states that the checker prints its own verdict and that the baseline differs between the published subset and the private working tree (`R-REL-1`). The same file was placed in the working tree together with the rest of `.github/`, `LICENSE`, `.gitattributes` and `.editorconfig`, so the publication source carries them instead of losing them on the next pass.
- Working tree (not published as such): `README.md` licence section corrected from MIT to PolyForm Noncommercial 1.0.0 with the same note as the published Russian README, and a «Проект в числах и измеренный полигон» section added. The working tree's doctrine checker still reports **19 checks, 0 errors, 0 warnings**.

### Changed (statistics re-measured from the sources, 2026-10-16)
- **Every count in the documentation was re-measured, not copied from earlier prose.** Measured on the published tree: **38** facade operations, **40** MCP tools (6 of them the live-database layer), 3 MCP resources + 1 URI template and 2 prompts, **14** CLI subcommands and **39** `query` operations, **16** tree-parsed languages (13 generic profiles + dedicated PHP/CSS/HTML extractors), **11** text-attached languages — Rust had been missing from the list — 55 mapped extensions, 32 admin-panel API routes, 7 runtime dependencies; `src/` is 50 files / 37 592 lines, PowerShell 5 files / 3 423 lines, the shipped tree 136 files / 3.5 MB; the portable suites are 6 with **164 checks, 0 failures**, and the defect register holds **61 findings** (43 of them counted by severity in the audited revision).
- `README.md`, `README.ru.md`: new **By the numbers** / **Проект в числах** section naming, for every count, the file it was measured from; the text-attached language list corrected to eleven; the benchmark section now says what actually ships — the navigation run's raw artifacts are *not* in the public tree (`R-REL-1`), while `bench/results/M2_rev-0012.json` and `M2_rev-0025.json` **are** the shipped artifacts of the graph metric (mean recall 0.771 → 0.917, median 1.0, precision 0.072 → 0.020, naive baseline 0.0); the checker's behaviour on the published subset is stated plainly (14 checks: 11 pass, 3 report files that are deliberately absent).
- `src/ragraf/facade.py`, `src/ragraf/mcp_server.py`: stale docstrings corrected — the facade announced «17 операций» where the file defines **38** (roadmap `R-DOC-1`), and the server listed 5 protocol methods where **12** are handled, with the tool/resource/prompt surface now named.
- Sync of the remaining documents: `docs/architecture/SPECIFICATION.md` (22 → **38** facade operations, **39** CLI ops), `docs/architecture/ACCEPTANCE.md` and `docs/ru/02_architecture/ACCEPTANCE.md` (M4-T1: the 17-operation scope is labelled as release 1), `docs/ru/02_architecture/TZ.md` (sync note), `docs/ru/skills/ragraf-core/SKILL.md` («39 инструментов и 37 операций» → **40 и 38** with the date), `docs/skills/ragraf-core/SKILL.md` and `reference/repair.md` (checker baseline), `docs/DEFECTS.md` (the register has grown to 60 findings; the 43-finding summary describes the audited revision), `ROADMAP.md` (`R-DOC-1` progress, `R-T1` 23 → **40**), and `tools/Invoke-ProjectDoctrine.ps1` (the rule's own description).
- The wiki was re-synced to the same numbers and gained the shipped-artifact benchmark plus the dogfooding measurement (RAGRAF indexing its own working tree).

### Added (repository presentation, 2026-10-16)
- `CITATION.cff` — machine-readable citation metadata (GitHub's «Cite this repository»), with the SPDX identifier `PolyForm-Noncommercial-1.0.0` verified against the SPDX licence list on 2026-10-16.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, enforcement contact shared with `SECURITY.md`).
- `.github/PULL_REQUEST_TEMPLATE.md` — the PR template that `CONTRIBUTING.md` and the previous changelog entry already promised while the tree did not carry it.
- `.github/ISSUE_TEMPLATE/config.yml` — the issue chooser: free-form reports are off, questions go to Discussions, benchmarks to the website, vulnerabilities to a private advisory.
- `.github/workflows/pages.yml` — builds `docs/` with MkDocs Material (pinned 1.6.1 / 9.7.7) and deploys it to GitHub Pages through the `github-pages` environment; `docs/index.md` is that site's landing page. Verified locally with the same command CI runs (`mkdocs build --strict`): 20 pages, no warnings, `sitemap.xml`, `404.html` and the search index present.
- `.github/workflows/codeql.yml` — CodeQL `security-and-quality` analysis of the Python sources, published to the code-scanning tab. Mechanical counterpart to the threat model already published in `SECURITY.md`.
- `.github/dependabot.yml`, `.github/release.yml` — pip and Actions updates as reviewable pull requests; release notes mapped onto the labels this repository uses.
- `.editorconfig`, `.gitattributes` — LF and UTF-8-without-BOM declared at repository level, and language statistics that describe the product (Python) rather than the prose around it.
- `assets/social-preview.png` (1280×640), `assets/og-image.png` (1200×630) — social preview and Open Graph artwork in the site's dark graphite/aurora palette.

### Changed (repository presentation, 2026-10-16)
- `README.md`, `README.ru.md`: the licence badge and the licence section said **MIT** while `LICENSE` and `pyproject.toml` had been PolyForm Noncommercial 1.0.0 since 2026-10-15 — corrected, with an explicit note that GitHub's licence badge reads «Other» because PolyForm is not in the catalogue GitHub detects. CI, release, docs-site and star badges added; navigation to the website, wiki and docs site; a «Use cases» table; the offline promise stated in the abstract.
- `README.ru.md` additionally: the abstract was still the English paragraph, and the documentation table pointed at `docs/02_architecture/…` and `AGENTS.md` — paths that do not exist in the published tree. Now it links `docs/ru/…` and `docs/DEFECTS.md`, and carries a table of contents like the English README.
- `pyproject.toml`: `Homepage` now points at the website; `Documentation`, `Wiki` and `Changelog` URLs added; keywords expanded from 6 to 18; classifiers include Python 3.14 and Windows.
- `CONTRIBUTING.md`: licence reference corrected to PolyForm; Discussions, the Code of Conduct and the security channel linked from the quick-links table.
- `mkdocs.yml`: `slugify_unicode` for the table of contents. With the default ASCII slugify every Cyrillic anchor is dropped and the Russian pages' own tables of contents break — measured: `mkdocs build --strict` aborted with 14 broken anchors in `ru/05_article`.

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

[Unreleased]: https://github.com/paulmann/RAGRAF/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/paulmann/RAGRAF/releases/tag/v0.2.0
[0.1.0]: https://github.com/paulmann/RAGRAF/releases/tag/v0.1.0
