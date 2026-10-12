# Technical Specification: RAGRAF

**Version:** 1.0 · **Date:** 2026-10-04 · **Project:** `<repository>`
**Basis:** `docs/00_plan/PLAN.md` *(not shipped)*, `docs/01_research/SPEC_INPUT.md` *(not shipped)*, [`ARCHITECTURE.md`](ARCHITECTURE.md)
**Product owner:** owner of the agent environment. **Executor:** the agent environment.
**Owner decisions fixed on 2026-10-04:** JVM (JDK + Joern) allowed · pilot `second PHP corpus` · embeddings `paraphrase-multilingual-MiniLM-L12-v2` · release 1 — PHP only.

> **Translation note.** Original Russian: «Техническое задание» (TZ). Requirement IDs are transliterated consistently: **ФТ-n → FT-n** (functional), **НФТ-n → NFR-n** (non-functional). Test IDs (`M1-T1` …) and metrics (`M1`…`M7`) are unchanged. The shipped product implements a superset of this release-1 spec (22 facade operations instead of 17, 16 tree-parsed languages instead of PHP-only); deltas are marked where relevant and summarized in [`ARCHITECTURE.md` §7](ARCHITECTURE.md#7-facade-operations-contracts).

---

## Contents

- [1. Purpose and goal](#1-purpose-and-goal)
- [2. Scope of work](#2-scope-of-work)
- [3. Terminology](#3-terminology)
- [4. Functional requirements](#4-functional-requirements)
- [5. Non-functional requirements](#5-non-functional-requirements)
- [6. Order of work (milestones)](#6-order-of-work-milestones)
- [7. Release 1 acceptance criteria](#7-release-1-acceptance-criteria)
- [8. Risks and measures](#8-risks-and-measures)
- [9. Delivered artifacts](#9-delivered-artifacts)
- [10. Limitations and caveats](#10-limitations-and-caveats)

---

## 1. Purpose and goal

The RAGRAF system creates and maintains an **auxiliary knowledge base about code** (graph + symbols + vector + lexicon) so that an LLM agent developing and modifying software **does not study the repository blind**, but receives verifiable answers: where it lives, who calls it, what will break, how it is done in a similar place.

**The goal across three lifecycle phases:**
1. before a change effort — index the code;
2. during the change effort — answer the agent from the base and **accumulate the differences** between the base and the code in a separate ledger;
3. after the change effort — reindex, bringing the base into agreement with the code.

**The key requirement the ledger exists for:** while differences accumulate, the agent must not draw conclusions from stale data **without knowing it**.

---

## 2. Scope of work

### 2.1 In release 1 scope

| # | Work item |
|---|---|
| 1 | PHP corpus indexing pipeline: AST, symbols, CPG, DB schema, routes, documentation |
| 2 | Three stores (graph, lexicon+vector, control) and a data schema with provenance |
| 3 | Facade: 17 operations (architecture §7), MCP server and CLI |
| 4 | Drift ledger: delta detection, `stale` marking, archiving on reindex |
| 5 | Revisions: immutable snapshot, `reindex --commit`, history |
| 6 | Agent integration regimen (project `AGENTS.md` + skill) |
| 7 | Measurement stand and capturing metrics M1–M7 |
| 8 | Documentation: run guide, error descriptions, measurement report |

### 2.2 Explicitly out of release 1 scope

- Languages other than PHP (Python, JS/TS, C#, PowerShell, SQL logic) — release 2. *(Shipped product already includes them — see README.)*
- Vulnerability search and taint analysis as a standalone goal.
- A full autonomous «code rewriter»: RAGRAF **answers**; the agent makes the edits.
- Web interface, multi-user mode, access rights, cloud. *(The shipped product later added a local single-user admin panel — out of this spec.)*
- Revision branching (several active indexes at once).
- Storing the index outside the project's working directory and shipping it automatically between machines.

---

## 3. Terminology

| Term | Meaning |
|---|---|
| **Corpus** | the set of project files to be indexed, per the rules of `config/project.yaml` |
| **`INDEX_REV`** | a named immutable index revision (`rev-0007`) |
| **Fact** | a graph node or edge carrying `evidence` (`path:line`) and `confidence` |
| **`stated` / `derived`** | an edge proven by an analyzer / inferred by a heuristic |
| **Ledger** | the append-only journal of differences between index and code |
| **`stale`** | a fact touched by a ledger entry and therefore not guaranteed current |
| **Degradation** | a revision mode without CPG (`capability: degraded`) |
| **Measurement** | a procedure from `ACCEPTANCE.md` that yields a number |

---

## 4. Functional requirements

Format: **requirement — acceptance criterion — how it is verified**. Verification refers to `ACCEPTANCE.md` (§ protocols) or to a direct test.

### 4.1 Indexing

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **FT-1** | Indexing starts on the explicit command `ragraf index --project <cfg>` and creates a new `INDEX_REV` | The command exits 0, creating `index/rev.json`, `graph.sqlite`, `lexvec.sqlite` and a `rev_history` row | Test M1-T1 |
| **FT-2** | PHP parsing: nodes `namespace/class/trait/interface/function/method/property/constant` with exact `path`, `start_line`, `end_line` | For 100 deliberately chosen symbols the line boundaries match the file (script check) | Test M1-T2 |
| **FT-3** | Symbol layer: definitions, references, implementations | For 30 hand-annotated symbols the usage set contains no false positives and loses no real ones | Test M2-T1 |
| **FT-4** | Call edges: `calls` (exact) and `calls_heuristic` (dynamic) separately | No edge has a `kind` without `provenance`; dynamic calls never land in `calls` | Test M2-T2 |
| **FT-5** | DB schema: tables, columns, types, keys, FK — from `information_schema` and migrations | All schema tables present; each with its `source` | Test M2-T3 |
| **FT-6** | Code-to-table links: `queries`, `references_table` from SQL literals | For 20 hand-picked SQL literals the tables are found; `SELECT *` does not spawn invented columns | Test M2-T4 |
| **FT-7** | Entry points: `web_route`, `http_endpoint`, `cli_command`, `cron_job` | For 15 hand-annotated entry points the handlers are found | Test M2-T5 |
| **FT-8** | Documentation is indexed: `doc`, `doc_section` + `documented_by` edge | The corpus's markdown files are present; the link is set where the document names a symbol or path | Test M2-T6 |
| **FT-9** | Text and structure are separated: comments/docblocks/README/commits live in `text_chunk`, not in structural facts | No structural fact contains comment text in `props`/`name` | Test M2-T7 |
| **FT-10** | Every fact carries provenance: `evidence_path`, `evidence_line`, `confidence`, `sources` | Sample of 1000 random edges: 100% have `evidence_path` and `confidence` filled; `stated` — 1.0 | Test M2-T8 |
| **FT-11** | Coverage report: parsed/partial/failed, edge counts by kind, conflicts, duplicates | `ragraf status` prints coverage; the sum over `file` equals the corpus file count (unparsed files do not disappear) | Test M1-T3 |
| **FT-12** | Degradation mode: with the CPG unavailable, indexing completes and the revision is marked `degraded` | Artificially disabling Joern → exit code 0, `joern_used = 0`, `slice`/`flow` refuse with the reason | Test M3-T1 |
| **FT-13** | Determinism: two builds of one corpus yield identical identifiers | `corpus_hash`, node/edge counts and the `edge_uid` sets match | Test M1-T4 |

### 4.2 Facade

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **FT-14** | The 17 operations of architecture §7 are implemented with the described inputs/outputs | Each operation has a test on valid input and on an empty result | Test M4-T1 |
| **FT-15** | No free-form graph query language is available to the agent | No operation accepts query text; an attempt to pass Cypher/CPGQL is rejected with an error code | Test M4-T2 |
| **FT-16** | Every answer carries the envelope: `index_rev`, `capability`, `drift`, `provenance`, `limits`, `stale_warnings` | No answer arrives without these fields | Test M4-T3 |
| **FT-17** | Answers contain `path:line` for every fact | Sample of 200 facts from answers: 100% have a valid `path` and a line within its range | Metric M6 |
| **FT-18** | `impact` separates proven and heuristic links | Two blocks in the answer; `mode: hard` contains no `derived` | Test M4-T4 |
| **FT-19** | `slice` returns a structural slice (nodes+edges+lines) without function bodies | No code lines in the answer; bodies are available only via `code` | Test M4-T5 |
| **FT-20** | `repo_map` fits the given token budget | At `budget_tokens = 2000` the actual size ≤ budget (`tiktoken` count) | Metric M4 |
| **FT-21** | The ability to answer «not found» | With no relevant result the operation returns an explicit flag and offers `live_read` instead of an approximate answer | Test M4-T6 |
| **FT-22** | Operations are available as MCP tools and via CLI | Tool names match operation names; the CLI returns the same JSON | Test M4-T7 |
| **FT-23** | Answer wording is neutral: no evaluations, no «previously established» | No evaluative constructions in the output; past conclusions are not served as fact | Output review on 50 answers |

### 4.3 Drift ledger and revisions

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **FT-24** | The index is unchanged within a revision: during a change effort, read operations do not modify `graph.sqlite`/`lexvec.sqlite` | After 200 read operations the store file hashes are unchanged | Test M5-T1 |
| **FT-25** | Delta detection is available via 5 methods: `hook:session-start`, `compare`, `git-diff`, `db-schema-diff`, `manual` | Each method creates a correct `drift_entry` | Test M5-T2 |
| **FT-26** | The ledger is append-only: no delete/update operations on entries | Deleting an entry is impossible through the facade; the schema contains no `DELETE` paths | Test M5-T3 |
| **FT-27** | Deduplication: re-detecting the same delta does not double the ledger | Double `compare` run without edits → entry count unchanged | Test M5-T4 |
| **FT-28** | `stale` marking: with a non-empty ledger, answers touching affected paths carry `stale_warnings` | For 20 affected paths, every answer touching them has the warning | Metric M5 |
| **FT-29** | A separate entry kind `slice_boundary` when code is extracted into a function | An extract-method edit yields a `slice_boundary` entry **and** a `modify` | Test M5-T5 |
| **FT-30** | DB schema change detection: an appeared/deleted column → a `schema_change` entry | A migration on the stand creates an entry with the table and column names | Test M5-T6 |
| **FT-31** | `live_read` shows how the file differs from the index | Fragments changed after the revision are highlighted; `provenance: mixed` | Test M5-T7 |
| **FT-32** | `reindex --commit` creates a new revision, archives the ledger, clears `stale_edge` | After the command: a new `INDEX_REV`, an empty ledger, an archive file created | Test M5-T8 |
| **FT-33** | The reindex archive is a human-readable «what changed in the knowledge» report | The report contains the sections: changes by file, what went stale, slice boundaries | Test M5-T9 |
| **FT-34** | Revision history is preserved: `rev_history` and `drift_archive` are not deleted on rotation | After 5 reindexings with `retain_revs: 3` — 5 history rows, 2 rotated revisions deleted from the stores | Test M5-T10 |
| **FT-35** | Delta entries are written only through the facade, not by the agent directly | Direct writes to `control.sqlite` are not a supported path; write operations are not exposed | Interface review + test |
| **FT-36** | `drift_ack` does not delete an entry but appends an acknowledgment | After `drift_ack` the entry is present with a filled `ack` | Test M5-T11 |

### 4.4 Configuration and security

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **FT-37** | Corpus configuration: languages, includes, excludes, chunk budget, revision policy | The config is read, validated, and affects file selection | Test M1-T5 |
| **FT-38** | Exclusions are mandatory and effective: vendor dirs, `.log`, `_bak`, `node_modules`, agent artifacts | Not a single `exclude` file in `file` | Test M1-T6 |
| **FT-39** | Secrets are not indexed: values from `secrets_exclude` are absent from graph, vector and logs | Search for value patterns (test secrets) yields 0 matches in all three stores and logs | Test M3-T2 |
| **FT-40** | Logs contain neither the contents of `secrets_exclude` files nor config values | Manual review + automated pattern search | Test M3-T2 |

### 4.5 CPG-dependent features (available because the JVM is allowed)

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **FT-41** | `slice` along the backward data flow for a PHP function | For 10 hand-annotated cases the slice contains the real data path and no extraneous nodes | Test M2-T9 |
| **FT-42** | Compression measurement: slice versus the symbol's surroundings | For 20 symbols the token ratio is computed; the result is fixed as a number, without referencing anyone else's percentages | Metric M4 |
| **FT-43** | `flow` (data flow between two nodes) — when a path exists, returns step-by-step evidence | For 10 annotated pairs the path is found or its absence is honestly reported | Test M2-T10 |

---

## 5. Non-functional requirements

| ID | Requirement | Acceptance criterion | Check |
|---|---|---|---|
| **NFR-1** | Works without administrator rights; installs only into user directories | All commands pass in a non-elevated session | Install + indexing run |
| **NFR-2** | Full pilot indexing (~162 K lines of PHP) ≤ 15 min | Measured full-build time, fixed in the report | Metric M7 |
| **NFR-3** | Reindexing after an edit — minutes, not hours | Measured on the «edit 5 files» scenario | Metric M7 |
| **NFR-4** | Fast reads: `symbol`/`references`/`callers` ≤ 200 ms; `impact` (depth 3) ≤ 1 s; `search` ≤ 1 s; `slice` ≤ 2 s | 95th percentile over 100 queries of each kind | Metric M7 |
| **NFR-5** | Determinism of identifiers (see FT-13) | See FT-13 | Test M1-T4 |
| **NFR-6** | The index is reproducible and deletable: `ragraf drop` + `index` restores the state | After a rebuild, metrics M1–M4 are within noise | Metric M7 |
| **NFR-7** | Dependencies are version-pinned; upgrading is a deliberate step | A dependency pin file + a version-check command | Test M1-T7 |
| **NFR-8** | The CPG memory limit is configurable and does not bring the machine down | The set limit is respected; on shortage — degradation, not a crash | Test M3-T1 |
| **NFR-9** | Project code — PowerShell 7.6+/Python 3.13 per the project rules: `#Requires`, utf8NoBOM, no BOM/CRLF in shipped files, no WinPS-compat | Linter without errors; BOM/CRLF check on all shipped files | Test M6-T1 |
| **NFR-10** | Index artifacts are isolated in `<project>/.ragraf/` and do not leak into the project repository | No new files outside `.ragraf`; a `.gitignore` template exists | Test M6-T2 |
| **NFR-11** | Crash recovery: an unfinished indexing does not corrupt the previous revision | Interrupting a build midway → the previous revision remains working | Test M3-T3 |
| **NFR-12** | Diagnosability: every answer is reproducible from `index_rev` + parameters | Repeating the same query on the same revision yields the same facts | Test M6-T3 |

---

## 6. Order of work (milestones)

| M | Content | Result | Depends on |
|---|---|---|---|
| **M1** | Skeleton: directory structure, config, three-store schema, CLI skeleton, revision versioning | `ragraf index` creates an empty revision; `status` works | — |
| **M2** | PHP extractors: tree-sitter → AST nodes; SCIP/PHPStan → symbols; DB schema; routes; documentation | Graph over the pilot without CPG; coverage report | M1 |
| **M3** | CPG: connecting Joern + JDK, calls, CFG/DFG, slices; degradation mode | `capability: full` on the pilot; `slice` works | M2 |
| **M4** | Vector and lexicon: per-symbol chunking, embeddings, FTS5, hybrid `search` | `search` with provenance; embedding reuse | M2 |
| **M5** | Facade: 17 operations, answer envelope, `impact`, `repo_map`, `code`, `live_read` | The full operation set + MCP server | M3, M4 |
| **M6** | Drift ledger: delta detection, `stale`, deduplication, `slice_boundary`, `reindex --commit` + archive | Ledger and reindexing work end-to-end | M5 |
| **M7** | Agent integration: project `AGENTS.md`, skill, the «index first» regimen | Running a real modification task with the index | M6 |
| **M8** | Measurements M1–M7 and the report: index versus baseline | Numbers for all metrics, conclusions, bottleneck list | M7 |

**Rule for every milestone:** a separate run on a pilot revision of 6–10 files (architecture P7), then on the full corpus; results are recorded in `docs/03_implementation/`.

---

## 7. Release 1 acceptance criteria

| # | Criterion | Threshold |
|---|---|---|
| K1 | All FT implemented and tests passed | 43/43 |
| K2 | All NFR fulfilled or explicitly re-documented with justification | 12/12 or a written deviation |
| K3 | Full pilot indexing fits NFR-2 | ≤ 15 min |
| K4 | Every fact in an answer has provenance | 100% (FT-10, FT-17) |
| K5 | The end-to-end scenario works: indexing → modification with the ledger → reindexing → archive | Passed on the pilot |
| K6 | Secrets did not reach the index or logs | 0 pattern matches |
| K7 | Metrics M1–M7 captured with the `ACCEPTANCE.md` protocol | All seven |
| K8 | No report output relies on a stale fact without a mark | Check on a sample of 50 answers |
| K9 | Linter and encoding checks without errors | 0 errors |

**Metric thresholds (non-blocking but mandatory to record):**

| Metric | Target value | Rationale for the target |
|---|---|---|
| M1 — first-query navigation accuracy | Measure the baseline and improve it; the absolute target is set after the first measurement | No literature reference exists |
| M2 — impact-analysis completeness | Match the measured ceiling **precision 69% / recall 79%** (LCIP, `arXiv:1512.07435`) on the pilot's historical commits | The only confirmed reference point |
| M3 — context savings (tokens and calls per task) | Improvement over baseline; the number is recorded | No direct literature reference |
| M4 — slice compression | Measure; **do not compare** with 68–70% / 90.93% (unconfirmed) | Own data |
| M5 — freshness (share of `stale` and incidents of relying on stale) | Incidents — **0**; `stale` share — informational | Our metric: absent from the literature |
| M6 — link verifiability | ≥ 99% of links valid | Measured automatically |
| M7 — update cost | Full ≤ 15 min, incremental ≤ 3 min | NFR-2/3 |

---

## 8. Risks and measures

| Risk | Probability | Impact | Measure |
|---|---|---|---|
| `php2cpg` yields an incomplete CPG on real code | Medium | Loss of `slice`/`flow` | The `degraded` mode is designed in; the call-coverage metric will reveal the loss; on failure — `slice` via SCIP/own rules |
| A full CPG build does not fit 15 min | Medium | Breaks NFR-2 | Narrow the CPG: changed files + their callers only (decision by measurement) |
| Joern needs more memory than available | Low | Build failure | The `-Xmx` limit in config; on shortage — degradation, not a crash (NFR-8) |
| The agent's work leaves the index silently «rotten» | Medium | False conclusions | FT-24, FT-28, FT-32: the index is immutable, facts are marked, reindexing is mandatory |
| False edges from PHP heuristics spoil answers | High | Trust erosion | `stated`/`derived` separation (FT-4, FT-18), confidence calibration on 20 examples |
| `sqlite-vec` changes its API (pre-v1) | Medium | Breaks the vector layer | Isolation behind one module; move to `qdrant-client` by threshold (measurement) |
| Secrets reach the index | Medium | Leak | FT-39/FT-40, the exclusion list, a pattern test |
| The ledger grows to unreadable volume | Medium | Harm from accumulation (AdaRepair-Mem: «more memory ≠ better») | Archiving on reindex, deduplication (FT-27), prioritization by affected paths |
| Revision rotation deletes needed history | Low | Data loss | History and archives are never deleted (FT-34) |

---

## 9. Delivered artifacts

| Artifact | Path |
|---|---|
| Concept and plan documentation | `docs/00_plan/PLAN.md` *(not shipped)* |
| Research reports | `docs/01_research/*.md` *(not shipped)* |
| Architecture | [`docs/architecture/ARCHITECTURE.md`](ARCHITECTURE.md) |
| Technical specification (this document) | [`docs/architecture/SPECIFICATION.md`](SPECIFICATION.md) |
| Measurement and acceptance protocols | [`docs/architecture/ACCEPTANCE.md`](ACCEPTANCE.md) |
| Per-milestone implementation reports | `docs/implementation/*.md` |
| System code | `src/` |
| Tests | `tests/` |
| Maintenance scripts (PowerShell 7.6+) | `scripts/` |
| Measurements and measurement artifacts | `bench/` |

---

## 10. Limitations and caveats

1. **The compression percentages from the source article (68–70% and 90.93%) are not used in this spec** — not confirmed by the primary source.
2. **The LeanGuard reference is not used** — no work by that name was found.
3. **All published reference points were obtained on C/C++ and on vulnerability search**; our metrics are captured on PHP and on modification tasks, so other people's numbers serve only as an order-of-magnitude guide.
4. **Without the CPG (degradation mode) the system honestly reports it** and does not serve `slice`/`flow`; the requirements for them are fulfilled at `capability: full`.
5. **Confidence thresholds require calibration** (the literature spread is 3× between corpora); until calibrated, the starting values from architecture §5.2 are used.
6. **Licensing constraints:** CodeQL is not part of the stack; Joern is Apache-2.0; `sqlite-vec`, `fastembed`, `tree-sitter`, SCIP tools — MIT/Apache-2.0; versions are pinned (NFR-7).

---

*Translated from the original Russian technical specification (v1.0, 2026-10-04). Requirement numbering is preserved; shipped-product deltas are marked inline.*
