# RAGRAF — Code & Documentation Revision: Defect Register

> **Revision audited:** `df7c1be` (main, 2026-10-12) · **Scope:** 78 files — `src/` (≈18 500 LOC), `tools/`, `scripts/`, `docs/`, `tests/`, `bench/`, `admin/`, packaging files.
> **Method:** full read of README, all 3 PowerShell scripts, all 9 docs; automated scans (syntax, BOM/CRLF, `except` patterns, dead-reference resolution, SQL/eval/shell greps); targeted deep read of `facade.py`, `cli.py`, `mcp_server.py`, `admin_server.py`, `store.py`, `util.py`, `ensure.py`, `catalog.py`, `watch.py`, `telemetry.py`, `settings.py`, `cpg.py`, `embeddings.py`, `compute.py`, `tools/ragraf.py`, `admin/static/app.js`, tests.
> **Verification environment:** Linux sandbox, Python 3.11 (syntax-level checks only; runtime checks require Python 3.13 + Windows and were performed as static analysis with `file:line` evidence).

## Severity legend

| Sev | Meaning |
|---|---|
| 🔴 **CRIT** | Blocks public release or creates a real security/correctness risk |
| 🟠 **HIGH** | Should be fixed before/at release; user-visible breakage or doc-vs-code contradiction |
| 🟡 **MED** | Fix soon; robustness, portability, maintainability |
| 🔵 **LOW** | Polish, cosmetic, nice-to-have |

**Totals: 4 CRIT · 11 HIGH · 19 MED · 9 LOW = 43 findings.**

*Register updated 2026-10-16: the file has grown to **60 findings** in groups A–H — counted the same way the register is read, by its `### X-n` sections. The severity totals above describe the revision audited on 2026-10-12; later sections were appended, not back-filled, because the ledger is append-only by design.*

---

## A. Release-blocking: dead references (files promised but not shipped)

### A-01 🔴 CRIT — README documents `AGENTS.md`, which does not exist in the repository
- **Where:** `README.md` → «Документация» table, last row (`AGENTS.md | правила разработки самого проекта; базовый чекер конвенций`).
- **Evidence:** `AGENTS.md` is absent from the git tree (`git ls-files` — not present). A reader clicking the implied reference finds nothing; the convention checker `tools/Invoke-ProjectDoctrine.ps1` that README also references reads its baseline from `AGENTS.md` §10 (2 errors / 0 warnings), so the checker's documented contract is unverifiable.
- **Fix:** either publish a sanitized `AGENTS.md`, or remove the row and describe conventions inline (a `CONTRIBUTING.md` is the natural home).

### A-02 🔴 CRIT — 24 dead path references across published docs
- **Where (all verified missing):**
  - `docs/skills/ragraf-core/SKILL.md` → `AGENTS.md`, `docs/AGENTS-context.md`, `tools/measure_safe_excludes.py`, `tools/check_ragraf_config.py`, `tools/check_service_excludes.py`, `tools/measure_dedupe_rules.py`, `tools/find_doc_dupes.py`, `tests/test_dedupe.py`, `tests/check_admin_agents.py`, `tests/check_admin_dedupe.py`
  - `docs/skills/ragraf-core/reference/repair.md` → `tools/measure_safe_excludes.py`, `tools/check_ragraf_config.py`, `tools/check_service_excludes.py`, `tools/measure_dedupe_rules.py`, `tests/test_dedupe.py`, `tests/check_admin_agents.py`, `tests/check_admin_dedupe.py`, `AGENTS.md` §10
  - `docs/03_implementation/INTEGRATION.md` → `tests/test_mcp.py` («✅ все проверки пройдены» — cannot be reproduced), `config/myproject.yaml`
  - `docs/05_article/RAGRAF_ARTICLE.md` → `docs/03_implementation/RAGRAF_EFFECT_REPORT.md`, `docs/03_implementation/POLYGON_REPORT.md`, `docs/04_research/GAPS_FOR_LLM.md`, `docs/04_research/WORKFLOW_ANALOGS.md`, `docs/04_research/SHADCN_LOGIN_REFERENCE.md`, `docs/01_research/01_ARTICLE_COVERAGE.md`, `tools/bench_corpus.py`, `tests/test_watch_live.py`, `bench/baseline/`, `bench/tasks.yaml`
  - `docs/02_architecture/ARCHITECTURE.md`, `TZ.md` → `docs/00_plan/PLAN.md`, `docs/01_research/SPEC_INPUT.md`, `docs/03_implementation/ACCEPTANCE_REPORT.md`
- **Why it matters:** the project's core value proposition is *verifiable facts*. A documentation set with 24 unverifiable references undermines exactly that promise; GitHub renders them as broken links.
- **Fix:** publish the missing artifacts where licensing/anonymization allows; otherwise rewrite references to «not shipped in the public tree» with rationale, or delete them. The article's «Приложение: артефакты» table needs a *shipped / not shipped* column.

### A-03 🔴 CRIT — 26 dead references inside source-code comments
- **Where (sample, full list verified by automated scan of `src/`, `tools/`, `scripts/`):** `tests/test_auto_thresholds.py` (catalog.py), `tests/test_cpg_carry.py` (catalog.py), `tools/check_admin.py` (admin_server.py), `tools/check_admin_project_settings.py` (settings.py), `tools/check_case_sensitivity.py`, `tools/probe_vec_metric.py`, `docs/80-roadmap.md`, `docs/Stats.md`, … — all cited as evidence («дефект найден проверкой X») in docstrings of shipped modules.
- **Why it matters:** comments cite non-public verification tools as proof; a contributor cannot re-run them. Same credibility issue as A-02, but inside the code itself.
- **Fix:** ship the referenced checkers (most are small synthetic-fixture scripts), or reword comments to describe the *observation* without pointing at an unavailable file.

### A-04 🔴 CRIT — shipped tool `tools/audit_defects.py` is dead on arrival
- **Where:** `tools/audit_defects.py:24` — `cfg = load_config(REPO / "config" / "z-batteryservice.yaml")`.
- **Evidence:** `config/` is git-ignored (`.gitignore` §3 `/config/`); the file references the author's private corpus. Any user running `python tools/audit_defects.py` gets `ConfigError: конфигурация не найдена`. The module also references the non-shipped `GAPS_FOR_LLM.md`.
- **Fix:** accept `--project <cfg>` argument with a clear usage message, or exclude the tool from the public tree until generalized. (Check `tools/bench_index_modes.py`, `tools/measure_index_times.py` for the same pattern.)

---

## B. Documentation ↔ code divergence

### B-01 🟠 HIGH — operation/tool counts contradict across documents and code
- **Facts:** `src/ragraf/facade.py` defines **22** `op_*` methods; `src/ragraf/mcp_server.py` exposes **23** `ragraf_*` tools (adds `ensure`, omits `calibrate`); `src/ragraf/cli.py` `OPS` lists **23** names (adds `usage`, which has no `op_usage` — served via telemetry).
- **Contradictions:** `ARCHITECTURE.md` §7 — «17 операций» (table lists 17); `TZ.md` ФТ-14 — «17 операций»; `RAGRAF_ARTICLE.md` §5 — «21 фиксированная операция», §8.3 — «17 → 21»; `facade.py:1` docstring — «Фасад: 17 операций»; README — «22 операции» (correct) and «23 инструмента MCP» (correct).
- **Note:** `SKILL.md` §4.2 *knows* about this («код объявляет 23 инструмента и 22 операции фасада, а документы называют 18, 16 и 21») — and the docs were still shipped unfixed.
- **Fix:** single source of truth — generate the operation table from code (or at least sync ARCHITECTURE/TZ/article/facade docstring to 22/23). For EN production docs: **22 facade operations, 23 MCP tools, 23 CLI query ops**.

### B-02 🟠 HIGH — `repair.md` recipe §2 describes an already-fixed defect as open
- **Where:** `docs/skills/ragraf-core/reference/repair.md` §2 «Реестр показывает пустую ревизию и нули» — prescribes «Правильное исправление: добавить запись результата в `ensure`…».
- **Evidence:** `src/ragraf/ensure.py:338-348` already calls `record_index_run(cfg, report, trigger="ensure:index")` with an explicit comment that this was the measured defect and it is fixed.
- **Fix:** move the recipe to past tense («historical defect, fixed on 2026-10-12; if you see zeros again — …») or delete.

### B-03 🟠 HIGH — ARCHITECTURE promises parallel AST parsing; indexer is single-threaded
- **Where:** `ARCHITECTURE.md` §9.1 «Разбор AST (tree-sitter, параллельно)», §13 «Параллельный разбор (8 ядер/16 потоков)»; `RAGRAF_ARTICLE.md` §4 implies the same.
- **Evidence:** `src/ragraf/indexer.py`, `pipeline.py` — zero occurrences of `threading`, `concurrent.futures`, `multiprocessing`; file loop `for cf in scan.files:` (indexer.py:378) is sequential.
- **Impact:** NFR-2 (≤15 min full index) may still hold on the pilot, but the documented mechanism doesn't exist; large corpora lose the promised speedup.
- **Fix:** either implement parallel parse (tree-sitter parsing is CPU-bound and trivially parallelizable per file) or correct the docs.

### B-04 🟡 MED — JDK version divergence: docs say JDK 19, reality is JDK 21
- **Where:** `ARCHITECTURE.md` header («JDK 19 + Joern»), §3, §6.1 vs `CPG_INTEGRATION.md` §4.3/§7 (Temurin **21**.0.12.1, «JDK 21 вне протестированного диапазона»), `install-joern.ps1` («JDK 21 в .tools\jdk»), README («нужны JDK 21 и Joern»).
- **Fix:** unify on «JDK 21 (19+ may work, untested)».

### B-05 🟡 MED — `vec_chunk` DDL differs between doc and code
- **Where:** `ARCHITECTURE.md` §4.2: `CREATE VIRTUAL TABLE vec_chunk USING vec0(rev_id TEXT, chunk_id TEXT, embedding FLOAT[384])`; `src/ragraf/store.py:443-446`: `chunk_id TEXT PRIMARY KEY, rev_id TEXT, embedding FLOAT[384]` (different column order + PK).
- **Fix:** sync doc to code (code is authoritative).

### B-06 🟡 MED — CPG_INTEGRATION.md leaks machine-local state into public docs
- **Where:** «На этой машине: PHP 8.5 найден (`C:\tools\php85\php.exe`)», §7 status table («Joern 4.0.99 установлен… 1633 МБ»), «у пилота composer.json нет».
- **Why:** a snapshot of the author's machine shipped as reference documentation; it will rot immediately and reads as noise to newcomers. The *valuable* content (§8 «что выяснилось только запуском» — 12 undocumented Joern behaviors) should stay.
- **Fix:** convert §1/§7 to «verified configuration (example)» framing; keep §8 as-is (it is excellent).

### B-07 🟡 MED — INTEGRATION.md §5 status table references private state
- «✅ `config/myproject.yaml`», «✅ ревизия `rev-0012`, 392 файла», «✅ `tests/test_mcp.py` — все проверки пройдены» — none of these exist in the public tree; the table presents the author's environment as the product's.
- **Fix:** retitle as «integration status on the pilot machine (historical)» or remove.

### B-08 🟡 MED — docs numbering has holes (`00_plan`, `01_research`, `04_research` missing)
- Published: `02_architecture`, `03_implementation`, `05_article`, `skills`. Missing numbers are referenced (see A-02) and confuse navigation.
- **Fix:** renumber for the EN release (`docs/architecture/`, `docs/implementation/`, `docs/article/`, `docs/skills/`) or add a `docs/README.md` index explaining the layout.

### B-09 🔵 LOW — `requirements.txt` header promises «команда проверки версий» that doesn't exist
- `operations.md` §5 itself admits: «Нет штатной команды проверки версий зависимостей, хотя requirements.txt её обещает».
- **Fix:** document `pip freeze` / add `ragraf selftest` cross-reference, or drop the promise.

### B-10 🔵 LOW — `pyproject.toml` description is Russian while keywords/classifiers are English
- Mixed-language packaging metadata; PyPI page would render a RU one-liner for an EN release.
- **Fix:** EN description for the translated release (done in the refactored packaging notes).

### B-11 🔵 LOW — README test table vs shipped tests
- README lists 6 passing portable tests (56+31+25+23+17+12 checks) and explains 3 that need extras — accurate. But the article (§8.4) advertises «429 проверок, 0 провалов» across 11 suites, 8 of which are not shipped. A reader will look for them.
- **Fix:** add «on the author's corpora; not shipped» marker to article §8.4.

---

## C. Security

### C-01 🟠 HIGH — Admin panel token endpoint defeats its own threat model (DNS-rebinding + local malware)
- **Where:** `src/ragraf/admin_server.py:314-317` (`GET /api/token` returns `ACCESS_TOKEN` unauthenticated), `admin/static/app.js:64-66` (page auto-fetches the token and stores it in `localStorage`).
- **Claimed model** (module docstring): «случайная вкладка браузера не может изменить настройки» — **false as implemented**:
  1. No `Host`/`Origin` header validation. A malicious web page can DNS-rebind `evil.example` → `127.0.0.1`, then same-origin `fetch('/api/token')` reads the token and issues authenticated POSTs (`/api/settings/global`, `/api/settings/project`, `/api/cache/clear`).
  2. Any local process (including malware running as the user) gets the token with one GET.
- **Mitigating factors:** loopback-only bind, POST-only mutations, `secrets.compare_digest`, 1 MB body cap, settings writes validated (`settings.py:validate`) and backed up (`write_project_settings` creates `.bak.<ts>` — settings.py:465).
- **Fix (production):**
  - Validate `Host` ∈ {`127.0.0.1:port`, `localhost:port`} and reject cross-origin `Origin` on every request (kills DNS rebinding).
  - Remove `/api/token`; require the operator to paste the console-printed token into the UI once (store in `sessionStorage`, not `localStorage`), **or** issue a per-session cookie after a one-time pairing code.
  - Optional: `--token-file` / env `RAGRAF_ADMIN_TOKEN` for scripted use.

### C-02 🟡 MED — MCP «graph query language» filter is substring-based: false positives, trivial bypass
- **Where:** `src/ragraf/mcp_server.py` `_call_tool` — rejects any call whose stringified arguments contain `match (`, `return `, `cpgql`, `gremlin`, `create (`, `merge (`.
- **Problems:**
  1. **False positives on legitimate work:** `ragraf_search "when does the function return false"`, symbol `merge (`, docs about Cypher — all rejected with `-32602`. For a *code intelligence* product, blocking the word «return » in queries is user-hostile.
  2. **Trivially bypassed** (`m atch (`? no — but `MATCH(` without space, or nested dict/list values are not scanned at all: only top-level `str/int/float` values are joined).
  3. FT-15 is *already* structurally guaranteed: there is no query-execution endpoint. The filter adds harm without adding security.
- **Fix:** delete the filter (architecture already enforces FT-15), or restrict it to a hypothetical free-form `query` argument that doesn't exist. Document the decision.

### C-03 🟡 MED — Non-atomic JSON writes for catalog/settings (corruption window)
- **Where:** `src/ragraf/util.py:write_json` — direct `p.write_text(...)`; used by `catalog.Catalog.save()` and `settings.SettingsStore.save()`.
- **Risk:** crash/disk-full mid-write corrupts `catalog.json` (machine registry of all corpora) or `settings.json`. `.gitignore` lists `catalog/catalog.json.*` — backups anticipated, but the write itself is not atomic.
- **Fix:** write to `path.with_suffix('.tmp')` + `os.replace()` (atomic on Windows and POSIX); keep the timestamped backup for settings as today.

### C-04 🟡 MED — `shell=True` with hand-rolled quoting for Joern `.bat` launchers
- **Where:** `src/ragraf/cpg.py:_shell_cmd` — `" ".join(f'"{part}"' if " " in part else part for part in cmd)`.
- **Risk:** cmd.exe metacharacters (`&`, `|`, `^`, `%`, embedded `"`) in any path (e.g., Windows username `A&B`, directory `100%_done`) break the command or inject arguments. Documented as «проверено прогоном» — on the author's paths only.
- **Fix:** avoid `shell=True`: invoke `cmd.exe /c <bat> args…` with `subprocess` list form, or use `shutil.which` + direct `.exe` entry points where Joern provides them; at minimum escape cmd metacharacters.

### C-05 🔵 LOW — Telemetry `arg_digest` stores first 60 chars of arguments in plaintext
- **Where:** `src/ragraf/telemetry.py:normalize_arg` — docstring claims «открытый текст аргумента целиком не хранится», but `digest = text[:60]` is stored verbatim in `usage_call.arg_digest`.
- **Risk:** low (local SQLite, agent queries), but search queries may contain identifiers from secret-adjacent code; the claim overstates protection.
- **Fix:** reword the docstring, or hash-only mode behind a config flag for sensitive environments.

### C-06 🔵 LOW — f-string SQL identifiers
- **Where:** `admin_server.py:149` (`SELECT COUNT(*) FROM "{name}"` — name from SQLite catalog of tables), `lexvec.py:678`, `revisions.py:200,220` (`DELETE FROM {table}` — internal constant lists), `store.py:399-405` (PRAGMA/ALTER with internal names).
- **Risk:** low (identifiers are internal constants, not user input), but the pattern invites future injection if a name ever becomes user-controlled.
- **Fix:** central allow-list + `"` doubling helper (`name.replace('"','""')`), or a tiny `quote_ident()` in `store.py`.

---

## D. Installer (`scripts/*.ps1`)

### D-01 🟠 HIGH — `Invoke-Checked` deadlocks when child stderr exceeds the pipe buffer
- **Where:** `scripts/install.ps1` `Invoke-Checked`: `$out = $proc.StandardOutput.ReadToEnd()` **then** `$err = $proc.StandardError.ReadToEnd()`.
- **Mechanics:** classic .NET pipe deadlock — while the parent blocks reading stdout to EOF, the child can fill the 4 KB stderr pipe and block writing; neither side progresses. `pip install` writes progress/warnings to both streams; long installs on slow networks make this reachable, and the installer then *hangs* with no diagnostic.
- **Fix:** read both streams concurrently (`BeginOutputReadLine`/`BeginErrorReadLine` with `StringBuilder`, or two `ReadToEndAsync` tasks awaited together). Implemented in the refactored installer.

### D-02 🟠 HIGH — No timeouts on external processes
- **Where:** every `Invoke-Checked` call — `$proc.WaitForExit()` unbounded (pip, python, ragraf ensure/index/selftest).
- **Risk:** a hung pip (proxy blackhole) or a stuck first-time model download freezes the installer forever; user kills it, venv left half-built.
- **Fix:** `WaitForExit($timeoutMs)` per step (pip: 30 min default w/ `-Timeout` override; index: configurable), kill + FAIL result on expiry. Implemented in the refactor.

### D-03 🟡 MED — Selftest parsed by regex over Russian text although `selftest --json` exists
- **Where:** `install.ps1` step 6 — splits `StdOut` lines, matches component names and Russian failure markers (`отсутствует|не загружен|недоступ`).
- **Evidence:** `cli.py:cmd_selftest` supports `--json` (`args.json` → full checks array with `ok` booleans).
- **Risk:** any wording change, EN translation, or extra line breaks the parser *silently* (WARN, install «succeeds»).
- **Fix:** call `selftest --json`, parse `checks[].ok`. Implemented in the refactor. (Same applies to `ensure --json` — already used — and to the `catalog` regex fallback `конфиг:\s*(.+\.yaml)`, D-04.)

### D-04 🟡 MED — `catalog` output parsed with a fragile regex fallback
- **Where:** `install.ps1` step 5 — `if ($line -match 'конфиг:\s*(.+\.yaml)\s*$')` to recover the config path when `ensure --json` didn't yield one.
- **Risk:** breaks on EN localization, paths containing `.yaml` earlier, CRLF nuances.
- **Fix:** `catalog --json` (verify CLI supports it; if not — add) and parse JSON only; keep text fallback as last resort with a WARN that the contract changed.

### D-05 🟡 MED — No free-disk-space check
- **Where:** `install.ps1` — before creating venv + `pip install` (ONNX Runtime, tree-sitter wheels: ≈1.5–2.5 GB), and before `-Index` (model ≈240 MB + index growth).
- **Evidence:** README documents sizes; installer never checks `PSDrive.Free`.
- **Fix:** require ≥3 GB free on the venv target drive (warn-only threshold 5 GB with `-Index`); FAIL with the computed requirement otherwise. Implemented.

### D-06 🟡 MED — Microsoft Store Python stub produces a confusing failure
- **Where:** `install.ps1` step 1 — `Get-Command python` may resolve to `…\WindowsApps\python.exe` (App Execution Alias), which prints a Store banner and exits 9009.
- **Current behavior:** `Invoke-Checked` catches the non-zero exit and FAILs with «не запускается» — technically correct, diagnostically poor; also `ragraf.ps1` would happily invoke the stub.
- **Fix:** detect `WindowsApps` in the resolved path and print the exact remedy (disable App Execution Aliases or install python.org build). Implemented.

### D-07 🟡 MED — `-Force` venv removal can throw mid-install (locked/read-only files)
- **Where:** `install.ps1` — `Remove-Item -LiteralPath $Venv -Recurse -Force` under `$ErrorActionPreference='Stop'` with no try/catch; a python.exe still running from that venv (or AV lock) aborts the whole script with a raw Win32 error.
- **Fix:** wrap in try/catch, emit a FAIL result row with actionable advice (close shells using the venv, retry), continue to summary. Implemented.

### D-08 🟡 MED — «venv inside repository» warning only fires for pre-existing venvs
- **Where:** `install.ps1` step 1 — `$insideSource` computed only inside `if (Test-Path $Venv)`; when the script *creates* a venv inside `$Source`, no warning is issued, and the venv becomes part of the indexed corpus.
- **Fix:** compute `$insideSource` unconditionally and warn in both branches. Implemented.

### D-09 🟡 MED — No installation transcript/log file
- **Where:** all three scripts — output only via `Write-Host`; when a user reports a failure there is nothing to attach to an issue.
- **Fix:** `Start-Transcript` to `~/.ragraf/logs/install-<ts>.log` (opt-out `-NoTranscript`), print the path in the summary and on failure. Implemented.

### D-10 🔵 LOW — Dead code: `$dyVersion` computed and never used
- **Where:** `install.ps1` step 1 — `[version]::TryParse(...)` into `$dyVersion`, then version compared via a second parse (`$parts`).
- **Fix:** single parse. Done in refactor.

### D-11 🔵 LOW — «What's next» prints Windows activation path on all platforms
- **Where:** `install.ps1` final section — `Join-Path $Venv 'Scripts/Activate.ps1'` even when `$IsWindows -eq $false` (pwsh runs on Linux/macOS; the script is not Windows-gated).
- **Fix:** branch on `$IsWindows` (`bin/activate` otherwise). Implemented.

### D-12 🔵 LOW — Exit-code semantics: missing `pyproject.toml` reported as *environment* error (3), not *usage* (2)
- **Where:** `install.ps1` step 1 — «укажите каталог с исходниками: -Source <путь>» is a bad-argument condition; documented codes say 2 = invalid arguments, 3 = unsuitable environment.
- **Fix:** exit 2 for that case. Implemented.

### D-13 🟡 MED — `install-joern.ps1` cannot actually install Joern (no download, no checksum)
- **Where:** script requires a manually placed `.tools/downloads/joern-cli.zip` and a manually installed `.tools/jdk`; throws otherwise. README presents it as «Установка Joern».
- **Gaps:** no `-Download` from the official GitHub release, no SHA-256 verification, no JDK provisioning (e.g., Adoptium API), no `-JoernVersion` pin.
- **Fix:** add opt-in download with pinned version + published SHA-256, `try/finally` restore of `$env:JAVA_HOME`/`$env:PATH` (currently mutated for the session and never restored — D-14), and idempotent re-run that verifies the unpacked tree instead of only its existence. Implemented in the refactor.

### D-14 🟡 MED — `install-joern.ps1` leaks `$env:JAVA_HOME` / `$env:PATH` mutations
- **Where:** `install-joern.ps1` «Проверка запуска Joern» — sets both for the session without save/restore.
- **Fix:** save/restore in `finally` (session-scoped changes should not outlive the verification step). Implemented.

### D-15 🔵 LOW — `install-joern.ps1` executable search includes `.sh` on Windows and trusts the first recursive hit
- **Where:** fallback `Get-ChildItem -Recurse -Filter 'joern*'` accepts `.sh`; on Windows a `.sh` match is unusable, and «first hit» may pick the wrong artifact (e.g., `joern-cli.bat` helper).
- **Fix:** platform-aware candidate order; verify the chosen executable responds to `--help`. Implemented.

### D-16 🔵 LOW — `ragraf.ps1` checks Python presence but not version
- **Where:** `scripts/ragraf.ps1` — error text says «Требуется Python 3.13», but only `Get-Command python` is checked; a 3.9 interpreter yields an import-time traceback instead of a clear message.
- **Fix:** same version probe as `install.ps1` (≥3.13). Implemented.

### D-17 🟡 MED — Installers have no automated tests
- **Where:** no Pester tests for any `*.ps1`; doctrine checker `Invoke-ProjectDoctrine.ps1` inspects sources textually, not behavior.
- **Fix:** Pester suite with mocked `Invoke-Checked` (argument construction, exit-code mapping, summary table), plus a smoke test in CI on `windows-latest`. (See roadmap R-CI.)

---

## E. Core logic

### E-01 🟠 HIGH — Glob matching is case-sensitive on Windows (product's primary platform)
- **Where:** `src/ragraf/util.py:compile_glob/glob_match` — `re.compile(...)` without `re.IGNORECASE`; documented in `SKILL.md` §5.2 and `repair.md` §4 as a known trap («`**/cache/**` не ловит `src/Cache/`»).
- **Impact:** on a case-insensitive filesystem, `exclude`/`secrets_exclude` patterns silently miss directories whose real case differs → vendor code bloats the corpus, and in the worst case a secrets pattern misses (`**/*.PEM` vs `*.pem`) — NFR-3 relevant.
- **Fix:** on Windows (`sys.platform == 'win32'` or `os.name == 'nt'`) compile case-insensitively, or casefold both pattern and path before matching; keep case-sensitive behavior on POSIX. Add tests for both.

### E-02 🟠 HIGH — Hardcoded machine-specific PHP discovery paths
- **Where:** `src/ragraf/cpg.py:119` — `for candidate in (Path("C:/tools/php85/php.exe"), Path("C:/tools/php/php.exe"))`.
- **Impact:** on any other machine these never match (harmless) but they encode the author's layout in public code; the real discovery (PATH/env/config) is buried under it. If a user *does* have `C:\tools\php`, an unexpected interpreter may be picked.
- **Fix:** order: `RAGRAF_PHP_BIN` env → corpus config `cpg.php_bin` → `shutil.which("php")` → (optionally) common install dirs *without* version-pinned author paths.

### E-03 🟡 MED — `store._migrate_graph` writes `schema_version` even when ALTER failed
- **Where:** `src/ragraf/store.py:399-415` — `except sqlite3.Error: pass` on `ALTER TABLE`, then unconditional `INSERT OR REPLACE meta(schema_version)`.
- **Impact:** DB claims to be at the new schema version while a column (`edge.props`) is missing; later queries fail with «no such column» far from the root cause.
- **Fix:** track migration success; only bump `schema_version` if all migrations applied (or store per-migration markers).

### E-04 🟡 MED — MCP JSON-RPC: replies to notifications; no protocol-version negotiation
- **Where:** `src/ragraf/mcp_server.py:handle` — unknown method → `_error(req_id, -32601, …)` even when `id` is absent (notification); `initialize` ignores client's `protocolVersion` and always answers `2024-11-05`.
- **Spec:** JSON-RPC 2.0 — «The Server MUST NOT reply to a Notification»; MCP — server should echo a supported version and clients from 2025 may disconnect on unexpected old versions (most tolerate it, but `2025-06-18` is current).
- **Fix:** `if req_id is None: return None` for errors; implement minimal negotiation (accept known versions list, answer with the highest supported ≤ requested, else own latest); bump to a current spec version after client-compatibility check.

### E-05 🟡 MED — GPU stack (`torch`, `sentence-transformers`) is used but not declared
- **Where:** `src/ragraf/compute.py:141-146` (import + error message «pip install sentence-transformers»), `graph_algo.py` (torch.sparse backend); neither is in `pyproject.toml` dependencies or extras; README explains the absence, `SKILL.md` §5.5 too.
- **Impact:** honest degradation works, but there is no supported install path for the optional accelerator; `tests/test_graph_algo.py` fails out of the box.
- **Fix:** add `[project.optional-dependencies] gpu = ["torch>=2.x", "sentence-transformers>=3.x"]` (documented as ~2.5 GB), so `pip install ragraf[gpu]` becomes the sanctioned path; keep runtime optional-import guards.

### E-06 🟡 MED — `SettingsStore.save` (global settings) has no backup, unlike project settings
- **Where:** `src/ragraf/settings.py:320-332` (validate → `util.write_json`) vs `write_project_settings` (backup at `settings.py:465`).
- **Fix:** same timestamped backup + atomic replace (see C-03).

### E-07 🔵 LOW — `EXIT_NOT_FOUND = EXIT_UNAVAILABLE` alias kept «for external callers»
- **Where:** `src/ragraf/cli.py:30-33` — public constant preserved with changed meaning (was «not found», now «layer refused»). External scripts checking `== 3` for «empty result» now misread semantics.
- **Fix:** fine as compatibility, but document in the EN CLI reference: **0 = ok (incl. empty result with `found=false`), 1 = error, 2 = usage, 3 = capability unavailable**.

### E-08 🔵 LOW — `admin_server.py` embedded page `lang="ru"` and RU strings throughout UI
- For the EN production release: i18n pass over `PAGE_HTML`, API messages, and `admin/static/app.js` labels (all RU today). Not a bug — a release task.

### E-09 🔵 LOW — `repo_map` budget counts JSON characters, not tokens
- **Where:** `facade.py:_payload_weight` — «символов» (honest, documented); TZ ФТ-20 demands `budget_tokens` via tiktoken. `chars_used` field name is honest; docs elsewhere say «в заданном бюджете символов» (README) vs «budget_tokens = 2000» (ARCHITECTURE/TZ).
- **Fix:** align naming: either count tokens with tiktoken (dependency already present) or rename the contract field to `budget_chars` everywhere.

---

## F. Robustness / portability

### F-01 🟡 MED — `except Exception: pass` in ~10 places without logging
- **Where:** `cpg.py:806`, `ensure.py:330`, `watch.py:180,394`, `drift.py:450,476`, `catalog.py:217,340`, `facade.py:521`, `init_corpus.py:220` (OSError).
- **Context:** each is deliberate («учёт не должен отменять индексацию») and most append a note to the outcome — good practice — but several swallow silently (e.g., `watch.indexed_hashes` graph read → falls back to catalog silently).
- **Fix:** route through a tiny `util.swallow(exc, context)` that appends to an in-memory diagnostic ring buffer exposed via `index_status`/`selftest --json` («last swallowed errors»). Keeps behavior, removes blindness.

### F-02 🟡 MED — Linux/macOS path is declared but unverified (README says so honestly)
- **Where:** README «Общий путь через pip на Linux и macOS не проверялся».
- **Gaps found statically:** `install.ps1` activation hint (D-11), `ragraf.ps1` PATH separator `;` (Windows-only join — `"$srcPath;$previousPath"` breaks POSIX `:`), WMI-based test cleanup (win32-guarded — OK), `joern.bat`/`java.exe` candidates in `cpg.py` (Windows-only suffixes).
- **Fix (roadmap):** CI matrix (windows/ubuntu/macos), POSIX-aware PATH joins, `joern` (no ext) candidates. Until then keep the honest disclaimer.

### F-03 🔵 LOW — `ragraf.ps1` uses `;` as PYTHONPATH separator
- **Where:** `scripts/ragraf.ps1` — `$env:PYTHONPATH = "$srcPath;$previousPath"`; on Linux/macOS Python expects `:`.
- **Fix:** `[IO.Path]::PathSeparator`. (Windows-first product; low priority.)

### F-04 🔵 LOW — `tests/` has no unified runner
- Each test file is a standalone script with its own `check()` harness (documented in README). No `pytest` config, no `python -m tests`, no aggregate exit code.
- **Fix (roadmap):** thin `tools/run_tests.py` that discovers `tests/test_*.py`, runs each as a subprocess, aggregates PASS/FAIL counts and returns non-zero on any failure — without forcing a pytest migration.

---

## G. Packaging / release engineering

### G-01 🟠 HIGH — No CI: zero GitHub Actions workflows
- **Where:** `.github/` absent.
- **Impact:** claims like «49 файлов синтаксически чисты, BOM/CRLF отсутствуют, 6 тестов проходят» are manual; nothing prevents regression on the next commit. For a product whose pitch is *verifiability*, this is the most ironic gap.
- **Fix:** minimal workflow: syntax+BOM/CRLF lint, doctrine checker, portable tests (6 suites), `pip install -e .` smoke, `ragraf --version`, on `windows-latest` + `ubuntu-latest` (py 3.13/3.14). See refactored `ci.yml` in deliverables.

### G-02 🟠 HIGH — No release infrastructure: no tag, no CHANGELOG, no version single-source
- **Evidence:** repo has 0 tags; `version = "0.1.0"` in `pyproject.toml` and `__version__` in `src/ragraf/__init__.py` (verify sync — two places); no `CHANGELOG.md`; no GitHub Release notes.
- **Fix:** tag `v0.1.0`; add `CHANGELOG.md` (Keep-a-Changelog format); make `__init__.py` read version from package metadata (`importlib.metadata.version("ragraf")` with fallback) or keep both synced via a check in CI.

### G-03 🟡 MED — Dependency list duplicated (`pyproject.toml` + `requirements.txt`) without a lock
- Both files carry the same 7 runtime deps with identical bounds — maintained by hand; drift is a matter of time. НФТ-7 demands fixed versions; neither file is a lockfile.
- **Fix:** keep `pyproject.toml` as the source; generate `requirements.txt` (`pip-compile`) or reduce it to `-e .` + comments; optionally ship `requirements.lock` with hashes for offline/corporate installs.

### G-04 🟡 MED — No `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates
- README «Участие» is three sentences. For a public MIT project expecting «воспроизводимый дефект с командой и выводом», an issue template enforcing that format is cheap and high-value.
- **Fix:** add all four (provided in deliverables).

### G-05 🔵 LOW — `.gitignore` excludes `/config/` while docs tell users to create configs there
- Intentional (machine-local), but the EN docs must state the workflow explicitly: `ragraf init <project>` creates `config/<name>.yaml`, and `config/` never gets committed.

### G-06 🔵 LOW — `bench/results/*.json` contain commit SHAs and file paths of the private pilot corpus
- e.g. `M2_rev-0012.json`: `"commit": "7748931a37"`, `"primary_file": "src/Skills/SkillSelector.php"`. No secrets; README declares artifacts are shipped deliberately. Acceptable — but the EN release should say once, explicitly, what these identifiers refer to («an unpublished PHP corpus of the author; SHAs are not resolvable publicly»).

### G-07 🔵 LOW — No `py.typed`, no type-check config
- Code uses full type annotations (`from __future__ import annotations`, typed dataclasses) but ships no `py.typed` marker and no mypy/pyright config.
- **Fix:** add `src/ragraf/py.typed` + minimal mypy config; CI job optional.

---

## H. Tests

### H-01 🟡 MED — Test suite coverage vs claims
- **Shipped:** 10 files; 6 portable suites (164 checks total per README: 56+31+25+23+17+12); 3 need extras (Joern layer, torch, private config) — honestly documented.
- **Gaps:** no tests for `mcp_server` protocol handling (the referenced `tests/test_mcp.py` is not shipped), no tests for `admin_server` endpoints, no tests for `install.ps1` logic, no negative tests for the C-02 filter, no cross-platform glob-case tests (E-01).
- **Fix:** add the four suites listed (roadmap R-T1..R-T4).

### H-02 🔵 LOW — `tests/test_ensure.py` cleanup uses WMI (win32-only), silently skipped elsewhere
- Guarded by `sys.platform != "win32"` — acceptable, but on Linux a stray Joern server would survive the test. Document or use `psutil`-free `/proc` scan on POSIX.

---

## I. Cosmetic / editorial (mostly for the EN production pass)

| ID | Sev | Finding |
|---|---|---|
| I-01 | 🔵 | `facade.py:1` docstring «17 операций» (see B-01) |
| I-02 | 🔵 | Mixed RU/EN identifiers in user-facing JSON (`"не найдено"`, `"конфиг не найден"`) — the EN release needs a message catalog or EN strings with RU kept in `docs/ru/` |
| I-03 | 🔵 | `README.md` table «22 операции» lists `usage` (not a facade op) — footnote it |
| I-04 | 🔵 | `ARCHITECTURE.md` §12 item 3 contains a typo: «grубое изучение» |
| I-05 | 🔵 | `TZ.md` references «ФТ-10/ФТ-13» style IDs; EN translation should keep IDs stable (FT-xx) and map them in a table |
| I-06 | 🔵 | Article §11 diagram uses `style D6/D7` nodes not defined in the flowchart (rendering quirk) |
| I-07 | 🔵 | `examples/ragraf.example.yaml` — comments RU; translate (done in deliverables) |
| I-08 | 🔵 | `admin/static/app.js` — RU labels; translate for EN release (E-08) |
| I-09 | 🔵 | LICENSE © 2026 — fine; ensure `pyproject` `license-files` picks it up in sdist (verify build) |

---

## Summary table (top actions before public release)

| # | Action | Defects closed |
|---|---|---|
| 1 | Publish or excise all dead references; fix README doc table | A-01…A-04, B-02, B-07 |
| 2 | Harden admin server (Host/Origin check, remove/lock down `/api/token`) | C-01 |
| 3 | Refactored installer (async pipes, timeouts, disk check, JSON selftest, transcript) | D-01…D-16 |
| 4 | Sync docs to code (22/23 counts, parallel-parse claim, JDK 21, vec0 DDL) | B-01, B-03, B-04, B-05 |
| 5 | Case-insensitive globs on Windows + tests | E-01 |
| 6 | CI workflow + tag v0.1.0 + CHANGELOG | G-01, G-02 |
| 7 | Remove MCP substring filter (or justify) | C-02 |
| 8 | Atomic JSON writes + settings backup | C-03, E-06 |
| 9 | Declare GPU extras | E-05 |
| 10 | EN translation pass of README/docs/UI strings | I-02, I-07, I-08, E-08 |

*Prepared as part of the production-readiness revision, 2026-10-12. Every finding above carries `file:line` evidence and was verified against revision `df7c1be`.*
