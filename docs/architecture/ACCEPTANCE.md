# Measurement and Acceptance Protocols

**Purpose:** make RAGRAF's benefit **measurable by number**, not «it got more convenient». Every metric has a procedure, a capture tool, an artifact and a threshold.
**Basis:** plan §2.3, spec §7. **Pilot:** `second PHP corpus`. **Second corpus:** `pilot PHP portal` (no git → some metrics are not captured there, see §3).

---

## 1. Measurement rules

1. **The baseline is captured BEFORE the index is introduced** and stored in `bench/baseline/`. A measurement taken afterwards is not a baseline.
2. **Identical tasks** for the baseline and for the index: wording, files, the «done» criterion — identical; the only difference is the available tools.
3. **Tasks are fixed in advance** in the list `bench/tasks.yaml`; tasks invented after seeing the index's answers do not count.
4. **Every measurement is an artifact**: a JSON with numbers and a markdown with the interpretation. Without an artifact the measurement does not exist.
5. **Token counting** — `tiktoken`; one and the same tokenizer is fixed for comparisons.
6. **No one else's percentages** as a target: literature reference points are used only as an order of magnitude, and that is stated next to the number.
7. **A measurement is reproducible**: the capture command is written into the artifact.

---

## 2. Measurement task set (draft, refined with the owner)

| ID | Type | Wording (abbreviated) | Checks |
|---|---|---|---|
| T-01 | Navigation | «Find the handler of setting X and show where the data comes from» | M1 |
| T-02 | Navigation | «Where is the API Y response formed and which fields does it return» | M1 |
| T-03 | Links | «Who calls service Z and what happens if its signature changes» | M1, M3 |
| T-04 | DB schema | «Which columns of table T are actually read by the code, and where» | M1, M6 |
| T-05 | Modification | «Add a date filter to list W» (a real small edit) | M2, M3 |
| T-06 | Modification | «Add a new parameter to settings S with persistence in the DB» | M2, M3 |
| T-07 | Modification | «Extract the repeated block into a method» (`slice_boundary` check) | M2, M5 |
| T-08 | Impact | «What will renaming method M affect» | M2 |
| T-09 | Impact | «What will adding column C to table T affect» | M2 |
| T-10 | Documentation | «What does the documentation say about module N» | M1, M6 |

The standard set size is **≥ 20 tasks**, of which ≥ 8 are edits.

---

## 3. «Metric × corpus» matrix

| Metric | `second PHP corpus` (has git) | `pilot PHP portal` (no git) |
|---|---|---|
| M1 navigation | yes | yes |
| M2 impact by history | **yes** (103 commits) | no → synthetic tasks only |
| M3 context savings | yes | yes |
| M4 slice compression | yes | yes |
| M5 freshness | yes | yes |
| M6 verifiability | yes | yes |
| M7 update cost | yes | yes |

---

## 4. M1 — navigation accuracy

**Question:** does the agent find the needed file and line on the first query?

**Procedure.**
1. For each task T-01…T-04, T-10 the «reference answer» is fixed by hand: the list of `path:line` that **must** be in the answer (compiled from the code, not from the system's output).
2. Baseline: the agent receives the task and works **only** through `rg` and file reads (the index is unavailable).
3. Experiment: the agent receives the same task where the first step is facade operations; `rg` is allowed only as a fallback.
4. Recorded: number of queries until the first correct `path:line`; full/partial/empty answer; files read; lines read.

**Artifact:** `bench/results/M1_<corpus>_<rev>.json` + table.

| Indicator | Baseline | With the index | Threshold |
|---|---|---|---|
| Share of tasks solved on the first query | measure | measure | improvement mandatory |
| Mean files read per task | measure | measure | ≥ 2× reduction |
| Completeness of the reference answer | measure | measure | ≥ 90% |

---

## 5. M2 — impact-analysis completeness (the main metric)

**Question:** can the index predict which files a change will actually affect?

**Procedure (gold set from history).**
1. From the pilot's 103 commits, **20 edit-commits** are selected satisfying: ≥ 2 files changed, and at least one of them is not the file holding the changed public method (i.e. naive «find the name» does not work).
2. For each commit the state **before** the commit is taken and an index is built from it.
3. The question «what will changing <symbol> affect» is posed, without path hints.
4. `impact` is called in three modes: `hard` (`stated` only), `full`, and **baseline** — manual search via `rg`.
5. Precision / recall / F1 are computed over files and over symbols:
   - **precision** = predicted and actually changed / predicted;
   - **recall** = actually changed and predicted / actually changed.

**Artifact:** `bench/results/M2_<rev>.json`, including the commit list and an error breakdown (what was missed and why: dynamic call, SQL string, `include` wrapper, config).

**Reference point:** the measured ceiling in the literature — **precision 69% / recall 79% / F1 55%** (LCIP, `arXiv:1512.07435`, 17 574 changes, Java, mutation simulation). Our case is different (real edits, PHP), so the comparison is an order-of-magnitude guide only; that is stated next to the number.

| Indicator | Baseline (`rg`) | `hard` | `full` |
|---|---|---|---|
| precision over files | measure | measure | measure |
| recall over files | measure | measure | measure |
| F1 | measure | measure | measure |

**A separate error breakdown is mandatory:** without it the number is useless. Miss classification: (a) dynamic call; (b) SQL/string; (c) `include`; (d) config dependency; (e) not found at all.

---

## 6. M3 — context and task-cost savings

**Question:** how many tokens and calls does an edit take with and without the index?

**Procedure.** For tasks T-05…T-07 (real small edits):
1. Baseline: the agent's usual work (`rg` + file reads).
2. Experiment: first step — the facade, then the edit.
3. Recorded: input-context tokens, tool-call count, files/lines read, outcome (the edit works / does not).

**Artifact:** `bench/results/M3_<corpus>_<rev>.json`.

| Indicator | Baseline | With the index |
|---|---|---|
| Median tokens per task | measure | measure |
| Median tool calls | measure | measure |
| Share of tasks solved without reading files in full | measure | measure |

**Caveat:** no direct comparison of a graph index against «just a big context» was found in the literature, so the target is our own baseline, not someone else's number.

---

## 7. M4 — slice compression

**Question:** how much context does `slice` save, and does `repo_map` fit its budget?

**Procedure.**
1. For 20 pilot symbols (varying size: 20 to 1000 lines) tokens are computed for: (a) the bodies of all linked symbols (depth-2 surroundings), (b) `slice`, (c) `repo_map` with a budget.
2. The ratios `tokens(slice) / tokens(surroundings)` and `tokens(repo_map) / tokens(whole corpus of the chosen module)` are computed.
3. FT-20 is checked separately: `repo_map` at `budget_tokens = 2000` does not exceed the budget.

**Artifact:** `bench/results/M4_<rev>.json`.

| Indicator | Value |
|---|---|
| Median slice compression | measure |
| 10th and 90th percentile | measure |
| Files > 1000 lines where compression is worst | list |

**Prohibition:** comparing the obtained number with 68–70% / 90.93% from the article — those percentages are not confirmed by the primary source (see `01_ARTICLE_COVERAGE.md`, *not shipped*). Only an order-of-magnitude mention is allowed.

---

## 8. M5 — freshness and protection from wrong conclusions

**Question:** how many times did the agent rely on a stale fact **without knowing it**?

**Procedure (end-to-end scenario).**
1. Index the pilot → `rev-N`.
2. A batch of 5–10 agent edits, including **one extract-method** and **one DB migration**.
3. After each edit — register the delta (`drift --scan`); **the index is not touched**.
4. Run the 20 tasks from the set: record how many answers had `stale_warnings`, and whether there were cases where:
   - the answer relied on a stale fact **without** a mark → **incident** (target: 0);
   - the answer contained a mark and the agent switched to `live_read` → correct behavior;
   - the agent saw the mark and used the index anyway → **second-kind incident** (target: 0).
5. `reindex --commit`; verify: a new revision is created, the ledger is empty, the archive contains the sections «changes», «what went stale», «slice boundaries».

**Artifact:** `bench/results/M5_<corpus>.json` + a copy of `drift/archive/*.md`.

| Indicator | Value | Threshold |
|---|---|---|
| Incidents of relying on stale without a mark | measure | **0** |
| Second-kind incidents | measure | **0** |
| Share of answers with `stale_warnings` on a non-empty ledger | measure | 100% for affected paths |
| `slice_boundary` entries on extract-method | measure | ≥ 1 |

**Why this is our own metric:** not a single work was found in the literature measuring an agent's memory degradation over time. We define and measure it ourselves — and that fact is stated in the report.

---

## 9. M6 — verifiability

**Question:** can the references in answers be confirmed automatically?

**Procedure.** For 200 facts from the answers of all operations:
1. does the file `evidence_path` exist in the revision's working tree;
2. does `evidence_line` fall inside the node's range;
3. does the fact's `index_rev` match the current revision (otherwise it must be `stale`).

**Artifact:** `bench/results/M6_<rev>.json`.

| Indicator | Threshold |
|---|---|
| Share of facts with an existing file | 100% |
| Share of facts with the line inside the range | ≥ 99% |
| Share of facts with the correct revision | 100% |
| Share of `live_read` references confirmed by file comparison | 100% |

---

## 10. M7 — indexing and update cost

**Procedure.**
1. Full pilot indexing: time by stage (AST, symbols, CPG, DB, routes, text, loading, embeddings). Each stage is a separate number.
2. Reindexing after editing 5 files: time and **the share of reused embeddings**.
3. Operation response measurement: 100 requests of each kind, 95th percentile.
4. Reproducibility check: `drop` + `index` → comparison of metrics M1–M4 and of identifiers (FT-13).
5. Memory check: peak consumption of the CPG process.

**Artifact:** `bench/results/M7_<rev>.json`.

| Indicator | Threshold (NFR) |
|---|---|
| Full pilot indexing | ≤ 15 min |
| Reindexing after editing 5 files | ≤ 3 min |
| `symbol`/`references`/`callers` p95 | ≤ 200 ms |
| `impact` (depth 3) p95 | ≤ 1 s |
| `search` p95 | ≤ 1 s |
| `slice` p95 | ≤ 2 s |
| Peak CPG memory | measure; the limit is configurable |
| Identifier match on rebuild | 100% |

---

## 11. Tests (summary map)

| Test | Checks | Requirements |
|---|---|---|
| **M1-T1** | revision creation | FT-1 |
| **M1-T2** | symbol boundaries (100 symbols vs the file) | FT-2 |
| **M1-T3** | coverage report, file sum | FT-11 |
| **M1-T4** | determinism (double build) | FT-13, NFR-5 |
| **M1-T5** | config reading and validation | FT-37 |
| **M1-T6** | exclusion effect | FT-38 |
| **M1-T7** | dependency version pinning | NFR-7 |
| **M2-T1** | symbol references on 30 annotated symbols | FT-3 |
| **M2-T2** | `calls` / `calls_heuristic` separation | FT-4 |
| **M2-T3** | DB schema and source | FT-5 |
| **M2-T4** | SQL literals → tables, `SELECT *` | FT-6 |
| **M2-T5** | entry points (15 annotated) | FT-7 |
| **M2-T6** | documentation and `documented_by` | FT-8 |
| **M2-T7** | text/structure separation | FT-9 |
| **M2-T8** | provenance on 1000 edges | FT-10 |
| **M2-T9** | data-flow slice on 10 annotated cases | FT-41 |
| **M2-T10** | `flow` on 10 node pairs | FT-43 |
| **M3-T1** | degradation without CPG (and memory) | FT-12, NFR-8 |
| **M3-T2** | secrets not in graph/vector/logs | FT-39, FT-40 |
| **M3-T3** | recovery after an interrupted build | NFR-11 |
| **M4-T1** | all 17 operations: valid input and empty result | FT-14 |
| **M4-T2** | refusal on an attempt to pass a query language | FT-15 |
| **M4-T3** | answer envelope completeness | FT-16 |
| **M4-T4** | proven/heuristic separation in `impact` | FT-18 |
| **M4-T5** | `slice` without function bodies | FT-19 |
| **M4-T6** | honest «not found» + `live_read` suggestion | FT-21 |
| **M4-T7** | MCP tools match the CLI | FT-22 |
| **M5-T1** | the index is unchanged under reads | FT-24 |
| **M5-T2** | 5 delta-detection methods | FT-25 |
| **M5-T3** | append-only (no deletion) | FT-26 |
| **M5-T4** | delta deduplication | FT-27 |
| **M5-T5** | `slice_boundary` on extract-method | FT-29 |
| **M5-T6** | `schema_change` on migration | FT-30 |
| **M5-T7** | `live_read` highlights differences | FT-31 |
| **M5-T8** | `reindex --commit`: new revision, clean ledger, archive | FT-32 |
| **M5-T9** | archive contents (3 mandatory sections) | FT-33 |
| **M5-T10** | rotation of 3 out of 5 with history preserved | FT-34 |
| **M5-T11** | `drift_ack` does not delete the entry | FT-36 |
| **M6-T1** | PowerShell linter and BOM/CRLF check | NFR-9 |
| **M6-T2** | artifact isolation in `.ragraf/` | NFR-10 |
| **M6-T3** | answer reproducibility by revision and parameters | NFR-12 |

---

## 12. Final acceptance report

Report form (`docs/implementation/ACCEPTANCE_REPORT.md`):

1. Summary table K1–K9 with the actual state.
2. Summary table of metrics M1–M7: baseline, result, threshold, verdict.
3. M2 error breakdown by class (dynamics, SQL, `include`, config).
4. Bottleneck list with numbers (what exactly is slow/inaccurate, and by how much).
5. Deviations from the spec with written justification (if requirements changed — stating why and by whose agreement).
6. What did **not** confirm from the expectations — a direct section, without smoothing.
7. Recommendations for release 2 (languages, tools, bottlenecks).

**Report rule:** every claim of «better» is accompanied by a number and a procedure from this document.

---

*Translated from the original Russian protocols document. Requirement IDs transliterated ФТ→FT, НФТ→NFR; test and metric IDs unchanged.*
