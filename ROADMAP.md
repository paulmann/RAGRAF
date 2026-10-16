# RAGRAF Roadmap

> Every item was derived from the 2026-10 production revision of the codebase (defect register: `docs/DEFECTS.md`). IDs are stable — the wiki, defect register and issue tracker reference them (`R-SEC-1`, `R-PERF-1`, …).
>
> **Priority:** 🔴 P0 — release blocker · 🟠 P1 — next release · 🟡 P2 — planned · ⚪ P3 — backlog/vision.
> **Effort:** S ≤ 1 day · M ≤ 1 week · L > 1 week.

---

## A. Release & repository hygiene

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-REL-1** | 🔴 P0 | M | **Close all 50 dead references**: publish the safe subset of referenced tools/tests (`check_admin.py`, `measure_safe_excludes.py`, `test_dedupe.py`, `test_mcp.py`, `bench_corpus.py`, …) as portable synthetic-fixture versions; excise or annotate the rest; remove the `AGENTS.md` row from the README doc table or ship a public `AGENTS.md`/`CONTRIBUTING.md` | A-01…A-04, B-07 |
| **R-REL-2** | 🔴 P0 | S | **Tag `v0.1.0`** + GitHub Release with notes generated from this roadmap's P0 list; add `CHANGELOG.md` (Keep-a-Changelog); single-source the version (`importlib.metadata` fallback pattern) | G-02 |
| **R-REL-3** | 🔴 P0 | S | **Publish the EN documentation tree** (this translation pass): README.md (EN primary) + README.ru.md (original), renumbered `docs/` without holes, `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates | B-08, G-04 |
| **R-DOC-1** | 🟠 P1 | S | Sync all doc counts to code: **38 facade ops / 40 MCP tools / 39 CLI query ops** everywhere. Re-measured 2026-10-16: `README.md` and `README.ru.md` (new «By the numbers» / «Проект в числах» section), `docs/implementation/INTEGRATION.md`, `docs/architecture/ARCHITECTURE.md` §7, `SPECIFICATION.md`, `ACCEPTANCE.md`, `TZ.md`, the article, the `ragraf-core` skill pages (EN + RU), the wiki, and the `facade.py` / `mcp_server.py` docstrings were synced. Still to do: `vec_chunk` DDL framing, JDK 21 wording in historical documents | B-01, B-04, B-05, I-01 |
| **R-DOC-2** | 🟡 P2 | M | **Generate the operation reference from code**: docstring/schema harvest → `docs/operations.json` → rendered tables in README/wiki; CI fails on doc↔code drift (the project's own principle: facts, not memory) | B-01 permanently |
| **R-DOC-3** | 🟡 P2 | S | `docs/README.md` index page + per-document «status» header (normative / historical / study), so a snapshot document like CPG_INTEGRATION §7 can't be mistaken for current state | B-06 |

## B. Security

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-SEC-1** | 🔴 P0 | M | **Admin panel hardening**: `Host`/`Origin` allow-list (kills DNS rebinding); remove `GET /api/token` auto-handout → operator pastes the console token once (`sessionStorage`) or one-time pairing code; optional `RAGRAF_ADMIN_TOKEN`/`--token-file` for scripts; document the threat model honestly | C-01 |
| **R-SEC-2** | 🟠 P1 | S | **Remove the MCP substring filter** («return », «match (», …): FT-15 is structurally guaranteed (no query endpoint exists); the filter only false-rejects legitimate searches. Keep a negative test proving query-shaped input can't reach any executor | C-02 |
| **R-SEC-3** | 🟠 P1 | S | **Atomic + backed-up writes**: `util.write_json` → temp file + `os.replace` (atomic on NTFS/POSIX); timestamped `.bak` for global `settings.json` and `catalog.json` (project configs already backed up) | C-03, E-06 |
| **R-SEC-4** | 🟡 P2 | M | **Joern .bat launch without `shell=True`**: `cmd.exe /c` with list-form args or direct JVM invocation; correct cmd-metachar escaping; tests with hostile paths (`A&B`, `100%`, quotes, cyrillic) | C-04 |
| **R-SEC-5** | ⚪ P3 | S | Telemetry privacy flag: `arg_digest` hash-only mode for sensitive environments (current 60-char plaintext digest is local-only but the docstring overstates protection) | C-05 |
| **R-SEC-6** | ⚪ P3 | M | Secret-scanner integration for corpora: warn (never store) when a file matching secret patterns *isn't* in `secrets_exclude` — extends NFR-3 from «we don't index secrets» to «we help you not miss them» | new |

## C. Core correctness

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-CORE-1** | 🟠 P1 | M | **Case-insensitive glob matching on Windows** (`re.IGNORECASE` when `os.name == 'nt'`, or casefold both sides): exclusions/secret masks must catch `src/Cache/` and `*.PEM` on the product's primary platform; portable tests for both behaviors | E-01 |
| **R-CORE-2** | 🟠 P1 | S | **PHP discovery without author's paths**: `RAGRAF_PHP_BIN` env → config `cpg.php_bin` → `shutil.which("php")`; delete hardcoded `C:/tools/php85` fallbacks | E-02 |
| **R-CORE-3** | 🟡 P2 | S | **Honest schema migration state**: bump `schema_version` only when all ALTERs applied; per-migration markers; surface «migration pending» in `index_status` | E-03 |
| **R-CORE-4** | 🟡 P2 | M | **MCP protocol hygiene**: no replies to notifications (JSON-RPC MUST NOT); protocol-version negotiation against `2025-03-26`/`2025-06-18`; structuredContent for tool results | E-04 |
| **R-CORE-5** | ⚪ P3 | S | Carry the docblock parameter contract (`array_shape`) into `code` answers, not only `symbol` | article §13.2 |
| **R-CORE-6** | 🟡 P2 | M | **Swallowed-exception ring buffer**: route the ~10 `except: pass` sites through `util.swallow(exc, ctx)`; expose «last swallowed errors» in `selftest --json`/`index_status` — keeps behavior, removes blindness | F-01 |

## D. Performance

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-PERF-1** | 🟠 P1 | L | **Parallel AST stage** (`ProcessPoolExecutor` over files; tree-sitter parsing is embarrassingly parallel): delivers the documented «8 cores/16 threads» claim; target ≥3× on 8-core for 10 K+ file corpora; keeps determinism (sorted batch order, content-hash IDs) | B-03 |
| **R-PERF-2** | 🟡 P2 | M | **Incremental CPG narrowing**: rebuild Joern only over changed files + their known callers when edits < threshold (architecture open question №1 — decided by measurement, gate: recall must not drop vs full rebuild on the M2 protocol) | ARCH §16.1 |
| **R-PERF-3** | 🟠 P1 | S | **Query-embedding LRU cache** (content-hash keyed, per model): hybrid search drops from ~1 s to FTS5-speed on repeated/similar queries — the measured 1000× outlier vs other ops | article §8.5 |
| **R-PERF-4** | ⚪ P3 | M | Normalize vectors at write time (keep exact-cosine read path for old revisions); re-measure M1/M4 before/after | article §13.2 |
| **R-PERF-5** | ⚪ P3 | L | ANN threshold study: `search` latency at 100 K/300 K/500 K chunks; pre-decide the `qdrant-client` file-mode migration behind `store.vec` (the isolation is already architectural) | ARCH §16.3 |

## E. CPG layer

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-CPG-1** | 🟠 P1 | S | **Publish the Joern layer's reproducible install** (done in this revision's refactored `install-joern.ps1 -Download` with SHA-256 pinning) + document the `php` precondition prominently in README install section | D-13 |
| **R-CPG-2** | 🟡 P2 | L | **Merge CFG/DFG into the graph** (444 K + 930 K edges already extracted, only calls merged today) → true PDG `slice` and a `flow` operation (FT-43 exists in the spec, unimplemented in the shipped facade) | article §13.2, spec FT-43 |
| **R-CPG-3** | ⚪ P3 | L | Taint-analysis experiments on the merged DFG (source→sink reachability with evidence) — explicitly out of release-1 scope, but the data is one merge away; keeps P3's «no vulnerability scanner» promise until measured | TZ §2.2 |
| **R-CPG-4** | 🟡 P2 | M | Localize the «next on empty iterator» export failure (cpg.bin built, export exit 1): reproduce on a fixture, capture Joern stderr into the stage reason | repair.md §3 |

## F. Packaging & dependencies

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-PKG-1** | 🟠 P1 | S | **Declare the GPU extra**: `[project.optional-dependencies] gpu = [torch, sentence-transformers]` (~2.5 GB, documented) so `pip install ragraf[gpu]` is the sanctioned path; `test_graph_algo.py` then has a supported environment | E-05 |
| **R-PKG-2** | 🟡 P2 | S | **Lock & check**: generate `requirements.txt` from `pyproject.toml` (pip-compile) or reduce it to a pointer; optional `requirements.lock` with hashes for offline/corporate installs; a `ragraf versions` command (closes the promise requirements.txt makes) | G-03, B-09 |
| **R-PKG-3** | ⚪ P3 | S | `py.typed` + minimal mypy config; the codebase is fully annotated — publish that fact | G-07 |
| **R-INST-3** | 🟡 P2 | M | **Offline install mode**: `-Wheelhouse <dir>` for air-gapped environments (wheels pre-fetched), pairs with R-PKG-2's hash lock | installer notes §5 |

## G. Testing & CI

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-CI-1** | 🔴 P0 | M | **GitHub Actions**: `windows-latest` + `ubuntu-latest`, Python 3.13/3.14 matrix — syntax + BOM/CRLF lint, doctrine checker, 6 portable suites, `pip install -e .` smoke, `ragraf --version`, installer dry-run with `-SummaryJson`, PSScriptAnalyzer with the shipped settings. A product whose pitch is verifiability must verify itself on every commit | G-01 |
| **R-T1** | 🟠 P1 | M | **MCP protocol suite** (shipped replacement for the referenced `test_mcp.py`): handshake, tools/list shape (**40** tools at the measured revision, 3 resources + 2 prompts), envelope fields, notification silence (after R-CORE-4), error codes | H-01 |
| **R-T2** | 🟠 P1 | M | **Admin server suite**: endpoints, token gate, validation rejections, backup creation on project-settings write, Host-check (after R-SEC-1) | H-01 |
| **R-T3** | 🟡 P2 | M | **Pester suite for installers** with mocked process invocation: argument construction, timeout path, exit-code matrix, summary JSON schema | D-17 |
| **R-T4** | 🟠 P1 | S | **Glob-case tests** on both platforms (fixtures with `Cache/` vs `cache/`) — gate for R-CORE-1 | E-01 |
| **R-T5** | 🟡 P2 | M | **Unified runner** `tools/run_tests.py`: discover `tests/test_*.py`, subprocess each, aggregate counts, non-zero exit on any failure, `--json` report; no pytest migration required (respects the standalone-script design) | F-04, operations.md §5 |

## H. UX, i18n & docs

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-I18N-1** | 🟠 P1 | L | **EN pass over product strings**: CLI messages, envelope `limits`/reasons, admin UI (`PAGE_HTML`, `app.js` labels), MCP tool descriptions; minimal message catalog (no framework), RU kept as a locale. The installer's bilingual text-fallbacks then retire | E-08, I-02, D-03 tail |
| **R-CLI-2** | 🟠 P1 | S | **`catalog --json`** (+ `status --json` already exists): removes the installer's regex-over-text fallback entirely | D-04 |
| **R-UX-1** | 🟡 P2 | S | `ragraf doctor`: one command printing environment + corpus + capability + last swallowed errors (R-CORE-6) + guard/lock state — the «paste this into the issue» command | new |
| **R-UX-2** | ⚪ P3 | M | `repo_map` budget in **tokens** (tiktoken is already a dependency) or rename the contract field to `budget_chars` consistently | E-09 |
| **R-PORT-1** | 🟡 P2 | M | **Verify Linux/macOS**: CI matrix (R-CI-1) + POSIX fixes (`ragraf.ps1` separator done; `joern` launcher candidates; activation hints done) → upgrade README from «not verified» to «supported (CI-verified)» | F-02 |

## I. Evidence & metrics (the project's soul)

| ID | P | Effort | Item | Closes |
|---|---|---|---|---|
| **R-EVID-1** | 🟠 P1 | L | **Live agent A/B**: the same 10–20 edit tasks (not navigation) run by a real agent with and without the index; protocol per ACCEPTANCE.md (fixed tasks, artifacts, both numbers published even if unfavorable). The article names this «the next level of evidence, more important than any optimization» — it stays the top research item | article §14 |
| **R-EVID-2** | 🟡 P2 | M | **Quality indicators in `index_status`**: vector-norm distribution, chunk-size histogram, signature-truncation count, coverage deltas (the admin panel computes them already — expose via the operation so agents/CI see them without the panel) | article §14.2 |
| **R-EVID-3** | 🟡 P2 | M | **Ship a public benchmark harness**: portable `bench_corpus.py` replacement on synthetic fixtures + the `bench/tasks.yaml` schema, so any user can reproduce M1/M3-style numbers on their own corpus | A-02, doctrine rule 7 |
| **R-EVID-4** | ⚪ P3 | L | Extend M5 (freshness incidents) to multi-session agent runs; publish the incident ledger format as a small standard — no literature measures memory degradation over time; RAGRAF owns this metric | ACCEPTANCE §8 |

## J. Long-range vision (⚪ P3, discussion welcome)

| ID | Item |
|---|---|
| **R-VIS-1** | **Second language depth**: bring one non-PHP language (Python or C#) to PHP-level verification (CPG frontend exists for both in Joern: `pysrc`, `csharp2cpg`) — the spec's release-2 promise |
| **R-VIS-2** | **Cross-corpus mode**: several repos in one answer space (microservices): separate `.ragraf/` per repo + a join layer over `catalog` — without violating per-corpus immutability |
| **R-VIS-3** | **LSP bridge**: expose `symbol`/`references` as an LSP server so humans get the same verified facts in-IDE that agents get over MCP |
| **R-VIS-4** | **Write-path**: `write_check` plans → generated migration skeletons with schema-diff evidence (still «RAGRAF answers, agent edits» — the answer just gets richer) |
| **R-VIS-5** | **Confidence calibration as a service**: `calibrate` currently needs manual samples; semi-automate from git history (co-change vs edge presence), publishing calibration artifacts per corpus |

---

## Sequencing suggestion

```
v0.1.0 (release blocker set):  R-REL-1/2/3 · R-SEC-1 · R-CI-1
v0.2.0 (correctness+speed):    R-CORE-1/2 · R-SEC-2/3 · R-PERF-1/3 · R-CLI-2 · R-PKG-1 · R-T1/T2/T4
v0.3.0 (depth):                R-CPG-2/4 · R-CORE-4/6 · R-I18N-1 · R-EVID-1/2 · R-T5 · R-PORT-1
v0.4.0+ (vision):              R-PERF-2/5 · R-CPG-3 · R-VIS-*
```

**Operating principle for this roadmap:** every item either closes a measured defect, fulfills a documented-but-unimplemented promise, or produces a new measurement. No item exists because it is fashionable.

---

*The roadmap is a living document: proposals are discussed in issues (see [Contributing](13-Contributing) — architecture conversations before large patches).*
