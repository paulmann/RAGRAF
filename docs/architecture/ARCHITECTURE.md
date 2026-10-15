# RAGRAF — Architecture (Stage 2)

**Status:** v1.0, ready for implementation.
**Inputs fixed by the product owner (2026-10-04):**
1. **JVM allowed** → the full CPG (AST+CFG+DFG+PDG) is available through Joern + JDK, on top of the base stack. *(Verified in production with JDK 21; the original decision named JDK 19 — see `CPG_INTEGRATION.md` §4.3.)*
2. **Pilot — `second PHP corpus`** (PHP, 103 commits, 162 366 lines, php 323/111 762), then `pilot PHP portal` (debugging), then `second corpus` (second language).
3. **Embeddings — `paraphrase-multilingual-MiniLM-L12-v2`** (384 dims, ~0.22 GB, ONNX via `fastembed`, no torch).
4. **First release — PHP only.** Other languages — release 2. *(Superseded in the shipped product: 16 tree-parsed languages, see README.)*

Related documents: `docs/00_plan/PLAN.md` *(not shipped)* · `docs/01_research/SPEC_INPUT.md` *(not shipped)* · [`SPECIFICATION.md`](SPECIFICATION.md) · [`ACCEPTANCE.md`](ACCEPTANCE.md)

---

## Contents

- [1. Purpose](#1-purpose)
- [2. Principles (not up for debate without an architecture revision)](#2-principles-not-up-for-debate-without-an-architecture-revision)
- [3. Components](#3-components)
- [4. Stores](#4-stores)
- [5. Data model](#5-data-model)
- [6. Extraction for PHP (release 1)](#6-extraction-for-php-release-1)
- [7. Facade operations (contracts)](#7-facade-operations-contracts)
- [8. Drift ledger](#8-drift-ledger)
- [9. Indexing and reindexing](#9-indexing-and-reindexing)
- [10. Revisions and storage](#10-revisions-and-storage)
- [11. Corpus configuration (`config/project.yaml`)](#11-corpus-configuration-configprojectyaml)
- [12. Agent integration](#12-agent-integration)
- [13. Performance and resources](#13-performance-and-resources)
- [14. Observability and diagnostics](#14-observability-and-diagnostics)
- [15. Error handling](#15-error-handling)
- [16. Open questions (resolved by measurement, not guesswork)](#16-open-questions-resolved-by-measurement-not-guesswork)

---

## 1. Purpose

RAGRAF is an auxiliary knowledge base about code that (a) spares the agent from studying the repository blind and (b) **does not lie to the agent** when the code has changed after the index snapshot.

Three operating modes:

| Mode | When | What happens |
|---|---|---|
| **Indexing** | before a change effort (`ragraf index`) | code snapshot → graph + symbols + vectors + lexicon → `INDEX_REV` |
| **Accompaniment** | during the change effort | the agent questions the facade; every edit is registered in the ledger; the index **does not change** |
| **Reindexing** | after the change effort (`ragraf reindex --commit`) | a new `INDEX_REV`; the ledger is archived into a «what changed in the knowledge» report |

---

## 2. Principles (not up for debate without an architecture revision)

| # | Principle | Rationale |
|---|---|---|
| **P1** | **The index is immutable within an `INDEX_REV`.** No partial incremental reindexing during a change effort | Otherwise facts from different moments in time mix in one answer with no way to tell them apart — the source of «wrong conclusions» |
| **P2** | **Staleness must never resolve to $null — only be shown.** A fact touched by the ledger is marked `stale` | Staleness indistinguishable from an error is worse than having no index |
| **P3** | **An edge without proof does not live in the graph as a fact.** `evidence` (`path:line`) and `confidence` are mandatory | Protection against «hallucinated links» inside the index itself |
| **P4** | **The agent gets no query language.** Only the fixed operations of §7 | A base model generates broken CPGQL (132/1278 and 19/1278 vs 1278/1278 — LLMxCPG, USENIX '25) |
| **P5** | **The graph is the primary source; vector and lexicon are supplementary.** «What is connected to what» is answered by the graph | Vectors catch syntactic similarity and cannot see interprocedural flows |
| **P6** | **Free text is separated from structural facts** (P5 in §4.3 of the spec) | ALIBI: comment injections suppress detection in >90% of cases; Words Speak…: phrasing shifts verdicts by 33.2% |
| **P7** | **The pilot revision is tiny but end-to-end.** First 6–10 PHP files from parsing to the agent's answer and the ledger entry | Debugging the pipeline on 162 K lines is a waste of time |
| **P8** | **Precision beats completeness at the first stage.** An honest «not found» + `live_read` is better than an approximate answer | Repoformer: the right not to search gives up to a 70% speedup; false confidence costs more |

---

## 3. Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│ L5  INTEGRATION   project AGENTS.md · skill · MCP server (primary input) │
├─────────────────────────────────────────────────────────────────────────┤
│ L4  FACADE        ragraf core: §7 operations · provenance · drift gate   │
├─────────────────────────────────────────────────────────────────────────┤
│ L3  STORES        graph.sqlite (nodes/edges/CPG)                         │
│                   lexvec.sqlite (FTS5 + vec0)                            │
│                   control.sqlite (revisions · coverage · ledger · arch.) │
├─────────────────────────────────────────────────────────────────────────┤
│ L2  LOADING       normalization → §5 schema → deduplication →            │
│                   idempotent revision load → invariant checks            │
├─────────────────────────────────────────────────────────────────────────┤
│ L1  EXTRACTORS    ┌ CPG (Joern + JDK) — calls, CFG/DFG/PDG, slices     │
│                   ├ Symbols (SCIP + PHPStan/Psalm PHAR) — definitions   │
│                   ├ Syntax (tree-sitter PHP) — fast skeleton,           │
│                   │   tokens for FTS/vector, incremental parsing        │
│                   ├ SQL/DB (MySQL information_schema + migrations)      │
│                   └ Text (md/comments/docblocks) — a separate layer     │
├─────────────────────────────────────────────────────────────────────────┤
│ L0  SOURCES       PHP repository · MySQL schema · migrations/*.sql · md │
└─────────────────────────────────────────────────────────────────────────┘
```

Components are separate processes (CLI), not libraries inside the agent's single process:

| Component | Technology | Role |
|---|---|---|
| `ragraf` CLI | Python 3.13 | entry point for humans and for the MCP server: `index`, `reindex`, `query`, `drift`, `status`, `calibrate` |
| Indexing pipeline | Python + external analyzer calls | L1–L2 |
| Joern wrapper | `joern-cli` invoked as a child process | CPG, CPGQL queries (internal only, never model-generated) |
| MCP server | Python, stdio | exposing §7 operations to the agent |
| SQLite stores | SQLite 3.50.4 + FTS5 + `sqlite-vec` | L3 |

---

## 4. Stores

Three files, each with its own role. The split exists because their lifecycles differ: the graph and vectors are large and rebuildable; control is small and **never deleted**.

```
<project>/.ragraf/
├─ index/
│  ├─ rev.json                 current revision, status, config hash
│  ├─ graph/graph.sqlite       nodes, edges, CPG, coverage
│  └─ lex/lexvec.sqlite        FTS5 (BM25) + vec0 (384-dim)
├─ control.sqlite              revisions, per-file coverage, ledger, archive — append-only
├─ drift/
│  ├─ LEDGER.md                human-readable journal (appended)
│  └─ archive/<rev>_<ts>.md    archives after reindexing
├─ config/
│  ├─ project.yaml             corpus configuration (paths, excludes, languages)
│  └─ confidence.yaml          confidence table (calibratable)
└─ logs/                       indexing and facade-call logs
```

### 4.1 `graph/graph.sqlite` — facts about the code

```sql
-- Revision and coverage
CREATE TABLE rev (
  rev_id TEXT PRIMARY KEY,          -- 'rev-0001'
  created_at TEXT NOT NULL,
  corpus_hash TEXT NOT NULL,        -- hash of the corpus input state
  config_hash TEXT NOT NULL,        -- hash of config/project.yaml
  extractor_versions TEXT NOT NULL, -- JSON: versions of Joern, scip-php, tree-sitter, PHPStan
  state TEXT NOT NULL               -- 'building' | 'ready' | 'failed'
);
CREATE TABLE file (
  rev_id TEXT NOT NULL, path TEXT NOT NULL,
  lang TEXT, lines INTEGER, sha256 TEXT NOT NULL,
  parse_status TEXT NOT NULL,       -- 'ok' | 'partial' | 'failed' | 'skipped'
  parse_note TEXT,
  PRIMARY KEY (rev_id, path)
);
-- Nodes
CREATE TABLE node (
  rev_id TEXT NOT NULL, node_id TEXT NOT NULL,
  kind TEXT NOT NULL,               -- see §5.1
  name TEXT, fqn TEXT,              -- fqn: Class::method
  path TEXT, start_line INTEGER, end_line INTEGER,
  props TEXT,                       -- JSON: signature, visibility, static-ness, etc.
  layer TEXT NOT NULL,              -- 'structural' | 'text'
  PRIMARY KEY (rev_id, node_id)
);
-- Edges
CREATE TABLE edge (
  rev_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  provenance TEXT NOT NULL,         -- 'stated' | 'derived'
  evidence_path TEXT, evidence_line INTEGER,
  confidence REAL NOT NULL,
  sources TEXT NOT NULL,            -- JSON: which analyzers produced the edge
  edge_uid TEXT NOT NULL,           -- deterministic hash (see §5.3)
  PRIMARY KEY (rev_id, edge_uid)
);
CREATE INDEX idx_edge_src ON edge(rev_id, src, kind);
CREATE INDEX idx_edge_dst ON edge(rev_id, dst, kind);
-- Free text — SEPARATE from structural facts (P6)
CREATE TABLE text_chunk (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  owner_node_id TEXT, path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
  text_kind TEXT NOT NULL,          -- 'comment' | 'docblock' | 'md' | 'commit'
  raw TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
-- Typed CPG subgraphs (populated only when Joern is present)
CREATE TABLE cfg_edge (
  rev_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  func_node_id TEXT NOT NULL, edge_order INTEGER
);
CREATE TABLE dfg_edge (
  rev_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  var_name TEXT, evidence_line INTEGER
);
-- External contracts
CREATE TABLE db_table (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, table_name TEXT NOT NULL,
  engine TEXT, row_estimate INTEGER, source TEXT NOT NULL,  -- 'information_schema' | 'migration'
  PRIMARY KEY (rev_id, table_id)
);
CREATE TABLE db_column (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, column_name TEXT NOT NULL,
  data_type TEXT, is_nullable INTEGER, column_key TEXT
);
CREATE TABLE db_fk (
  rev_id TEXT NOT NULL, from_table TEXT NOT NULL, from_column TEXT NOT NULL,
  to_table TEXT NOT NULL, to_column TEXT NOT NULL
);
CREATE TABLE web_route (
  rev_id TEXT NOT NULL, node_id TEXT NOT NULL, route TEXT NOT NULL,
  method TEXT, evidence_path TEXT, evidence_line INTEGER
);
-- Invalidation and coverage
CREATE TABLE invalidated (
  rev_id TEXT NOT NULL, path TEXT, node_id TEXT, reason TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE TABLE coverage (
  rev_id TEXT NOT NULL, metric TEXT NOT NULL, value REAL, note TEXT
);
-- Additional indexes
CREATE INDEX idx_node_fqn ON node(rev_id, fqn);
CREATE INDEX idx_node_path ON node(rev_id, path);
CREATE INDEX idx_text_owner ON text_chunk(rev_id, owner_node_id);
```

**Why `fqn` is a separate column and not «inside JSON».** The `symbol` and `references` operations are the most frequent; their selects must be index-backed.

### 4.2 `lex/lexvec.sqlite` — text search

```sql
-- Lexicon: exact names, paths, SQL literals, identifiers.
-- IMPORTANT: all content columns are TOKENIZED forms, not the original text:
-- fts5 stores tokens, so code cannot be read back from ft_chunk.
-- The column count here is part of the contract: the bm25() weights in
-- lexvec.py number exactly the same, and snippet() addresses a column BY
-- NUMBER (measured 2026-10-12: snippet(ft_chunk, 4, ...) lands in
-- code_tokens and returns «def op code ...»).
CREATE VIRTUAL TABLE ft_chunk USING fts5(
  rev_id UNINDEXED, chunk_id UNINDEXED, path UNINDEXED,
  symbol_tokens, code_tokens, sql_tokens, text_tokens,
  tokenize = 'unicode61'
);
-- Raw chunk text — a SEPARATE table, not a column of ft_chunk.
-- Added 2026-10-12: an answer must return the fragment in one call
-- (`search --with-text`), and the second-step address must resolve without
-- hitting disk (`code --entry-ids`). A column inside fts5 cannot do this:
-- an eighth column would silently shift all bm25 weights, and sqlite3 drops
-- UNINDEXED columns from the index, so text is not readable by number.
-- Line 0 of the text is the chunk's service header («function f (path:line)»);
-- code starts at line 1 and matches chunk_map.start_line.
CREATE TABLE chunk_text (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  path TEXT, text TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
-- Vectors: 384 dimensions (paraphrase-multilingual-MiniLM-L12-v2)
CREATE VIRTUAL TABLE vec_chunk USING vec0(
  chunk_id TEXT PRIMARY KEY, rev_id TEXT, embedding FLOAT[384]
);
-- Chunk ↔ source mapping
CREATE TABLE chunk_map (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,        -- 'symbol' | 'file' | 'table' | 'route' | 'doc'
  path TEXT, node_id TEXT, start_line INTEGER, end_line INTEGER,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE TABLE vec_meta (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  model TEXT NOT NULL, model_version TEXT, chunk_sha256 TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
```

> **Translation note (doc↔code sync):** the `vec_chunk` DDL above is corrected to match the shipped `src/ragraf/store.py` (`chunk_id TEXT PRIMARY KEY` first). The original RU document showed a column order without the primary key.

**Important:** `chunk_id` includes a revision-independent content hash (see §5.3), so **vectors do not have to be recomputed** for unchanged chunks on reindexing — the main source of time savings on rebuilds.

**Consciously accepted limitations:**
- `sqlite-vec` declares «pre-v1, expect breaking changes» → all access is hidden behind one module `ragraf.store.vec`; replacing the engine will not touch the spec.
- `sqlite-vec` has no ANN index (KNN scan) → above ~500 K chunks, a move to `qdrant-client` in file mode is evaluated. The threshold goes into a measurement, not into code preemptively.

### 4.3 `control.sqlite` — revisions, coverage, ledger (append-only)

```sql
CREATE TABLE rev_history (
  rev_id TEXT PRIMARY KEY, parent_rev TEXT,
  state TEXT NOT NULL,              -- 'ready' | 'failed' | 'archived'
  created_at TEXT NOT NULL, sealed_at TEXT,
  corpus_hash TEXT NOT NULL, files INTEGER, nodes INTEGER, edges INTEGER,
  joern_used INTEGER NOT NULL       -- 0/1: was the CPG available
);
CREATE TABLE drift_entry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  index_rev TEXT NOT NULL,
  ts TEXT NOT NULL,
  path TEXT NOT NULL,               -- or 'db://schema', 'git://commit'
  kind TEXT NOT NULL,               -- add|modify|delete|rename|schema_change|slice_boundary
  old_hash TEXT, new_hash TEXT,
  symbols_added TEXT, symbols_removed TEXT, symbols_changed TEXT,  -- JSON arrays of names
  edges_invalidated TEXT,           -- JSON array of edge_uid
  detected_by TEXT NOT NULL,        -- hook:session-start|compare|git-diff|db-schema-diff|manual
  commit_sha TEXT,
  ack TEXT,                         -- JSON: {by, ts} — who acknowledged the delta
  note TEXT,
  dedupe_key TEXT NOT NULL UNIQUE   -- (path, old_hash, new_hash, kind)
);
CREATE TABLE drift_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_rev TEXT NOT NULL, to_rev TEXT NOT NULL,
  archived_at TEXT NOT NULL, entries INTEGER NOT NULL,
  report_path TEXT NOT NULL
);
CREATE TABLE calibration (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, kind TEXT NOT NULL,   -- 'threshold' | 'confidence' | 'embedding'
  params TEXT NOT NULL,             -- JSON
  sample_size INTEGER NOT NULL, note TEXT
);
CREATE TABLE stale_edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  index_rev TEXT NOT NULL, edge_uid TEXT NOT NULL,
  reason TEXT NOT NULL, drift_entry_id INTEGER NOT NULL,
  acked_at TEXT
);
```

`stale_edge` materializes which edges stopped being trustworthy because of the ledger. It exists so `drift` answers fast, without recomputing intersections on the fly.

---

## 5. Data model

### 5.1 Nodes (`node.kind`)

| Group | Kinds | Source |
|---|---|---|
| Structure | `repo`, `file`, `namespace`, `class`, `trait`, `interface`, `enum` | CPG + tree-sitter |
| Code | `function`, `method`, `property`, `constant`, `parameter`, `sql_literal` | CPG + SCIP |
| Database | `db_table`, `db_column`, `db_view` | `information_schema` + migrations |
| Entry points | `web_route`, `http_endpoint`, `form`, `cli_command`, `cron_job` | routing heuristics (`$_GET/$_POST`, `include` graph, SSI) + configs |
| Knowledge | `doc`, `doc_section`, `commit`, `config_key` (**without values**) | md parser, git, config scanner |

### 5.2 Edges (`edge.kind`) and their trustworthiness

The `provenance` column is mandatory and takes exactly two values:
- **`stated`** — the edge is proven by an analyzer: an exact line of code, a concrete declaration, a concrete DB access. `confidence = 1.0` by default.
- **`derived`** — the edge is inferred by a heuristic or statistics (dynamic call, name match, inference from history). `confidence` comes from `confidence.yaml`.

| Kind | Type | Example of evidence |
|---|---|---|
| `contains`, `defines` | stated | `src/Pipeline/PipelineEngine.php:120` |
| `imports`, `extends`, `implements`, `overrides` | stated | declaration line |
| `calls` | stated | call line + declaration match |
| `calls_heuristic` | derived | variable-function, `call_user_func`, `include` wrapper |
| `reads`, `writes`, `flows_to`, `returns`, `type_of` | stated (CPG) | DFG edge with a line number |
| `queries`, `references_table` | stated | SQL literal → table |
| `fk_to` | stated | `information_schema` |
| `handles`, `renders`, `submits`, `posts_to` | derived | name/path binding with reduced confidence |
| `tested_by`, `documented_by`, `changed_in`, `mentions` | stated | test class, md link, commit |
| `similar_to` | derived | embedding proximity (never served as a structural fact) |

`confidence.yaml` — the starting table (calibrated on ~20 examples, FT-11 of the spec):

| Category | Initial value |
|---|---|
| Direct statics (CPG, declarations, `information_schema`) | 1.00 |
| Unambiguous heuristic (single candidate by name, `include` with a literal path) | 0.60 |
| Ambiguous heuristic (several candidates, dynamic path, magic method) | 0.40 |
| Inference from history/embeddings | 0.30 |

### 5.3 Identifiers

- `node_id` = `sha1(kind|fqn|path|start_line)` — **the line component is mandatory**: two identical functions in different places are different nodes. The node's name remains `fqn`, and `symbol` works by it.
- `edge_uid` = `sha1(src|dst|kind|evidence_path|evidence_line)` — **without the line an edge is not reproducible**, and then it can be neither confirmed nor invalidated.
- `chunk_id` = `sha1(content_sha256|chunk_kind|model)` — **deliberately without revision and without path**: renaming a file or reindexing does not force embedding recomputation.
- `corpus_hash` = `sha1` of the sorted list of `(path, sha256)` of all accounted files — the «input has not changed» signal.

---

## 6. Extraction for PHP (release 1)

### 6.1 Four sources and their roles

| Source | Provides | Role | Failure → |
|---|---|---|---|
| **Joern (`php2cpg`)** + JDK | AST+CFG+DFG+PDG, calls, slices, data flows | Primary for links and slices | Degradation (see §6.4) |
| **SCIP (`scip-php`) / PHPStan / Psalm** | definitions, references, implementations, types | Name resolver and «who references what» | tree-sitter + Joern remain |
| **tree-sitter PHP** | file skeleton, symbol boundaries, tokens for FTS/vector, incremental parsing | Always available; insurance and line markup | Indexing impossible → revision error |
| **`information_schema` + `migrations/*.sql`** | tables, columns, keys, relations | External contracts | whatever is in the migrations gets indexed |

**Evidence priority rule on conflict:** `information_schema` > migrations > CPG > SCIP > tree-sitter heuristic. A conflict is **never resolved silently** — a `conflict.*` metric is written to `coverage`, and the edge gets a mark in `sources`.

### 6.2 Typical PHP problems and how they are covered

| Problem | Solution | Confidence |
|---|---|---|
| `include`/`require` with a literal path | `imports` edge by the normalized path | stated, 1.0 |
| `include` with a variable/concatenation | candidate enumeration by config `glob` rules | derived, 0.40–0.60 |
| `include` inside a condition | edge + conditionality flag in `props` | stated de facto, but marked conditional |
| Variable-function, `call_user_func`, `eval` | `calls_heuristic` across all same-name candidates; with several — 0.40 | derived |
| Magic methods `__call`/`__get` | link with a `magic` mark, not as an ordinary call | derived, 0.30 |
| SQL in strings (the pilot: 52 files, 327 occurrences) | literal parsing: `references_table` to tables from `information_schema` | stated on exact table-name match |
| `SELECT *` and dynamic SQL assembly | edge to the table only; columns are «unknown» (an explicit marker in the answer, not invented columns) | derived, 0.40 |
| Superglobals `$_GET/$_POST/$_REQUEST` | entry-point nodes: `http_endpoint` + `flows_to` from them (CPG) | stated |
| One class in multiple copies (our projects: Editor-PHP in 5+ copies) | path exclusions §11 + duplicate metric | — |

**Fundamental decision:** heuristic edges **are included** in the graph (otherwise the agent cannot find links in dynamic PHP at all), but as a **separate kind** (`calls_heuristic`) and always with reduced confidence. Operations requiring guarantees (`impact` in «hard» mode) return them in a separate section.

### 6.3 What the graph will not see (and the agent will be told so directly)

Everything that comes from outside static analysis: values from the DB, computed class names, runtime configuration, the real call order under tag-based inheritance, SSI includes. The facade answer's wording: `limit: "static analysis; runtime dynamics not covered"`. This is a mandatory element of the answer, not an optional one.

### 6.4 Degradation when Joern is unavailable

Joern unavailable (JVM broken, JDK updated, timeout, out of memory on 162 K lines of PHP) — the pipeline **must not fall over**:

| Mode | Condition | Available | Marked |
|---|---|---|---|
| `full` | CPG built | everything, including slices and data flows | — |
| `degraded` | CPG not built | AST links, symbols, DB schema, routes, lexicon, vector | `rev_history.joern_used = 0`; **every facade answer carries `capability: degraded`**; `slice` and `flow` return a refusal with the reason |

Rule: **degradation is never silent.** An agent that does not know it has no slices will build conclusions on an incomplete picture.

---

## 7. Facade operations (contracts)

> **Translation note (doc↔code sync):** this section historically listed **17** operations (release-1 scope). As counted in the sources on 2026-10-16, the shipped facade implements **38** operations, exposed as **40 MCP tools**: five tools have no facade operation of their own (`context`, `ensure`, `task`, `usage`, `use_project`), and three facade operations are not exposed as tools (`calibrate`, `gate`, `task_set`). The five operations added after the spec — `write_check`, `policy_trace`, `class_usage`, `freshness`, `catalog` — are described in the README capability table and `docs/skills/ragraf-core/reference/operations.md`. Requirement IDs below keep the original numbering (ФТ-n → FT-n).

All operations return a common «envelope». The uniform format is requirement FT-10/FT-13: the agent always sees what it is trusting.

```json
{
  "op": "impact",
  "index_rev": "rev-0007",
  "capability": "full",
  "drift": { "files": 3, "symbols": 7, "entries": 4 },
  "provenance": "index",
  "limits": ["static analysis; runtime dynamics not covered"],
  "result": [ /* ... */ ],
  "stale_warnings": [
    { "path": "src/Pipeline/PipelineEngine.php", "reason": "modify after rev-0007",
      "since": "2026-10-04T14:32:00+03:00", "suggest": "live_read" }
  ]
}
```

`provenance` values: `index` (index only) · `live` (file reads only) · `mixed` (both; each record marked). `stale_warnings` is populated **automatically** for all ledger-affected paths; `suggest` is mandatory.

| # | Operation | Input | Output (essence) | Implementation (release 1) |
|---|---|---|---|---|
| 1 | `search` | `{query, kinds?, lang?, limit?}` | hybrid search: lexicon (FTS5) + vector, RRF merge; every hit with `chunk_id`, `path:line`, `score`, `why` | SQLite + fastembed |
| 2 | `symbol` | `{name \| fqn, path?}` | node(s): kind, file, lines, signature, who declared | index by `fqn` |
| 3 | `references` | `{node_id or fqn}` | all usage sites: `path:line`, line context | SCIP + tree-sitter |
| 4 | `callers` | `{fqn, depth=1..3, hard_only?}` | callers with confidence, stated/derived separately | CPG + heuristics |
| 5 | `callees` | `{fqn, depth=1..3, hard_only?}` | callees | CPG |
| 6 | `impact` | `{node_id or path or table_name, mode: hard\|full}` | affected files/symbols/tables/routes/tests, split by provability | graph + sql links |
| 7 | `slice` | `{node_id, direction: back\|forward, mode}` | **structural slice**: nodes+lines+edges with evidence, **without function bodies** | CPG (Joern) |
| 8 | `code` | `{node_id or path, style: compact\|signatures}` | symbol bodies on request (only on explicit request — otherwise the RepBench principle) | tree-sitter |
| 9 | `repo_map` | `{scope?, budget_tokens=2000, detail}` | repository map: modules → files → key symbols, ranked by connectivity and graph «centrality» | graph |
| 10 | `schema` | `{table?}` | tables, columns, keys, source (DB/migrations), divergences | `information_schema` + migrations |
| 11 | `table_usage` | `{table}` | who reads/writes the table: symbols, SQL literals, files | SQL literals + CPG |
| 12 | `routes` | `{path?}` | routes/entry points and their handlers | heuristics + configs |
| 13 | `index_status` | `{}` | revision, time, volumes, coverage, degradation, ledger size | control + graph |
| 14 | `drift_report` | `{since?}` | divergence journal: files, symbols, affected edges | control |
| 15 | `drift_ack` | `{entry_ids, note?}` | acknowledge deltas as accounted (appends `ack`, does not delete the entry) | control |
| 16 | `live_read` | `{path, start?, end?}` | read the current file — **with mandatory highlighting** of fragments differing from the index | filesystem + comparison |
| 17 | `calibrate` | `{kind, samples}` | recompute thresholds/confidence levels, write to `calibration` | control |

**Contract prohibitions:** arbitrary graph queries (FT-4), writing to the index through the facade (FT-9), returning a fact without `evidence` (FT-7), silently serving `derived` next to `stated` without separation (P2/spec).

---

## 8. Drift ledger

### 8.1 Invariants

- **I1.** A revision's index is unchanged until `reindex --commit`.
- **I2.** The ledger is append-only: an error is corrected by a new entry, not by overwriting or deletion.
- **I3.** A facade answer with a non-empty ledger cannot **silently** rely on an affected fact: either `stale_warnings`, or `provenance: live`.
- **I4.** An entry with `kind = slice_boundary` (extracting code into a function/method, changing slice borders) is recorded **separately** from `modify` — this is the only kind of change that durably breaks the graph (RepBench/LLMxCPG: a slice is robust to renaming but not to border changes).
- **I5.** After `reindex --commit` the archive contains the answer to «what changed in the knowledge», not just a file list.

### 8.2 Delta detection

| Method | When | Provides |
|---|---|---|
| `hook:session-start` | agent session start | the whole `drift_report`: what changed since the revision |
| `compare` | on the `ragraf drift --scan` command | full comparison: file `sha256` vs `file.sha256` + symbol list via tree-sitter |
| `git-diff` | when git is present (the pilot has it) | fast scan of changed files + commit link |
| `db-schema-diff` | before `impact`/`schema` | compare `information_schema` with the graph (a column appeared/disappeared) |
| `manual` | human/agent call | entry with an explanation |

### 8.3 Lifecycle

```
1. reindex --commit → rev-0008 → ledger empty, stale_edge empty
2. agent edits → compare/git-diff → drift_entry (add/modify/…) → LEDGER.md
3. edits that move slice borders → a separate slice_boundary entry
4. any facade answer → stale_warnings for affected paths → the agent decides: live_read or index
5. reindex --commit → rev-0009 → ledger → archive/<from>_<to>.md → the arithmetic of knowledge changes
```

Step 4 is the heart of the system: it is what prevents the agent from concluding on a stale fact — **without forbidding** the work.

### 8.4 Archive contents (human-readable report)

```markdown
# Reindexing rev-0007 → rev-0008 · 2026-10-04 21:40

Ledger changes: 4 · files: 3 · symbols affected: 7 · stale edges: 12

## What appeared/changed
- `src/Favorites.php` (add): 12 symbols, 3 new routes
- `src/Auth/AuthService.php` (modify): `verify`, `$ttl` changed

## What went stale in the knowledge between revisions
- 12 `calls` edges from `AuthService#verify` — rechecked and confirmed in rev-0008
- 2 `handles` edges for `Favorites#index` — not confirmed (route renamed)

## Slice boundaries
- `slice_boundary`: code extracted from `PipelineEngine#run` into `applyStage` — slices around `run` changed
```

The last section is the practical implementation of conclusion I4: the agent sees that the slice around a function no longer matches what it was.

---

## 9. Indexing and reindexing

### 9.1 Pipeline

```
0. Version and availability checks (Joern/JDK, scip-php, PHP, tree-sitter) → capabilities
1. Corpus scan: file selection, excludes, sha256, languages              → corpus_hash
2. AST parse (tree-sitter): symbol boundaries, tokens, lines
3. Symbols (SCIP + PHPStan/Psalm): definitions, references, types
4. CPG (Joern): calls, CFG/DFG/PDG, slices; on failure → degraded
5. External contracts: information_schema + migrations/*.sql → tables/columns/FK
6. Entry points: routing, forms, CLI/cron, SSI
7. Documentation: md → doc/doc_section, documented_by link
8. Stream T1: nodes + edges → graph.sqlite (in batches)
9. Stream T2: chunks → FTS5 + embeddings → lexvec.sqlite (reuse by chunk_sha256)
10. Coverage, conflicts, duplicates → coverage
11. Invariants + determinism (see §9.3) → rev.state = 'ready'
```

Streams T1 and T2 run in parallel: the graph does not depend on vectors.

> **Translation note (doc↔code sync):** the shipped indexer parses files **sequentially** (no thread/process pool in `indexer.py`); «parallel parsing» below describes the target mechanism, not the current implementation. Closing this gap is roadmap item **R-PERF-1**.

### 9.2 Incremental reindexing

The owner's JVM permission does not cancel principle P1: **incremental reindexing is allowed only at the moment of `reindex`**, not during a change effort.

Within one reindexing:
- AST/tree-sitter — incrementally via `Tree.edit` where a saved tree exists (release-1 implementation: saving trees for hot files is optional, see measurement);
- symbols — for changed files;
- **CPG — in full** (a rebuild on 162 K lines, time-limited: NFR-2);
- **embeddings — only for chunks whose `chunk_sha256` changed** (the main saving);
- DB schema — from `information_schema`, always.

**Expected budget (verified by measurement, not declared as fact):** full pilot build ≤ 15 min (NFR-2), reindexing after a small edit — minutes.

### 9.3 Determinism (NFR-4)

- All `node_id`/`edge_uid`/`chunk_id` derive from content, not from time or traversal order.
- The file list is sorted; loading proceeds in batches in deterministic order; no UUIDv4 and no timestamps inside identifiers.
- Check: building the same revision twice → `corpus_hash`, node/edge counts and the `edge_uid` set match.

---

## 10. Revisions and storage

**Decision: full revisions, no deltas.** Space is not the constraint (~1131 GB free on the pilot machine), and delta logic is a source of subtle errors in identifiers and in «which revision is this from?» answers.

Data isolation rule: everything in `graph.sqlite` and `lexvec.sqlite` is marked `rev_id`; the «current» revision comes from `rev.json`. **Exactly one** revision can be active; there are no parallel builds.

Retention policy: `retain_revs: 3` in the config; on exceeding, the oldest revision is deleted from the stores, **but `rev_history` and `drift_archive` remain forever** (this is the history of knowledge; it is never deleted).

---

## 11. Corpus configuration (`config/project.yaml`)

```yaml
project: second PHP corpus
root: <working directory>/second PHP corpus
languages: [php]                # release 1
include: ["**/*.php", "**/*.md", "db/migrations/*.sql"]
exclude:
  - "**/.git/**"
  - "**/node_modules/**"
  - "**/vendor/**"
  - "**/.zwork/**"
  - "**/var/**"
  - "**/*_bak*"
  - "**/Editor-PHP-*/**"        # vendored editor copies
  - "**/*.min.js"
secrets_exclude:                # NFR-3: exclusion only, no masking
  - "**/config/*.local.php"
  - "**/.env"
  - "**/.env.*"
  - "**/*.key"
  - "**/auth.json"
db:
  dsn_from: "src/Core/Config.php"   # where to take credentials from (values are NOT indexed)
  schema_source: ["information_schema", "db/migrations/*.sql"]
embedding:
  model: paraphrase-multilingual-MiniLM-L12-v2
  dim: 384
chunking:
  by_symbol: true
  max_chunk_lines: 120            # for giants (the pilot has a 9222-line file)
index:
  retain_revs: 3
```

**Secrets restriction.** Values from `secrets_exclude` never reach even `text_chunk`. For `db.dsn_from`, only **the fact** «this symbol references a config» is indexed, without values. That is NFR-3 in execution.

> **Known limitation (shipped code):** glob matching in `util.py` is **case-sensitive**, including on Windows. `**/cache/**` will not exclude `src/Cache/`. See `docs/skills/ragraf-core/reference/repair.md` §4 and roadmap item **R-CORE-1**.

---

## 12. Agent integration

1. **MCP server** — the primary channel: §7 operations as tools, names matching the table (`ragraf_search`, `ragraf_impact`, …).
2. **CLI** `ragraf` — for indexing, reindexing, debugging and manual checks; the MCP server is itself a wrapper over this core.
3. **The regimen in the project's `AGENTS.md`** — the agent's order of actions:

```
1. ragraf_index_status   → learn the revision, degradation, ledger size
2. ragraf_drift_report   → if the ledger is non-empty, account for divergences BEFORE any conclusions
3. answer the task via ragraf_symbol / callers / impact / slice
4. coarse repository study (rg, file reads) — only when the index answered
   «not found» or a fact is marked stale
5. after edits: ragraf drift --scan (register the delta). Do not update the index.
6. when the change effort is done: ragraf reindex --commit
```

4. **RAGRAF Skill** in the agent environment: a short instruction «index first, then `rg`», with query examples and editing rule №4.

Item 5 is what answers the owner's direct requirement: **during development we do not touch the index; we accumulate differences**; reindexing is a separate act of «the change effort is finished».

---

## 13. Performance and resources

| Operation | Expectation | How it is ensured |
|---|---|---|
| Full pilot indexing (162 K lines of PHP) | ≤ 15 min (NFR-2) | Parallel parsing (8 cores/16 threads) *(target; see §9.1 note — shipped code is sequential)*, streaming load, batches; CPG is the most expensive part, measured separately |
| Reindexing after an edit | minutes | embedding reuse by `chunk_sha256`; CPG rebuilds in full (the constraint is stated explicitly) |
| `symbol`, `references`, `callers` | ≤ 200 ms | indexes by `fqn`, `src/dst` |
| `impact` (depth 2–3) | ≤ 1 s | recursive CTEs over `edge` |
| `search` (hybrid) | ≤ 1 s | FTS5 + KNN scan of 384-dim vectors |
| `slice` | ≤ 2 s | CPG query inside Joern |
| Writing a delta to the ledger | ≤ 100 ms | append-only |

Memory: Joern on this volume of PHP is the most demanding part; the pilot server has 64 GB, the limit is set by the `-Xmx` flag and lives in the config (measured on the first run).

---

## 14. Observability and diagnostics

- **`ragraf status`**: revision, `capability`, coverage by language, node/edge counts, ledger size, timings of recent operations.
- **Coverage** — mandatory metrics landing in `coverage`: parsed/unparsed, `partial`, `calls_heuristic`, conflicts (`conflict.*`), file duplicates, chunks without an owner symbol, edge counts by `sources`.
- **Logs** — `logs/`, size-rotated; **without the contents of secret-bearing files** and without config values.
- **Answer tracing**: every facade answer can be reproduced from `op` + the envelope (`index_rev`, parameters) — the support for metric M6 (verifiability).

---

## 15. Error handling

| Situation | Behavior |
|---|---|
| Joern does not start / times out | Revision `degraded` (`joern_used = 0`), `slice`/`flow` refuse with the reason. Indexing does not fall |
| A file did not parse | `parse_status = 'failed'` + reason; the file **does not disappear** from `file` (otherwise «coverage» would lie) |
| Partial parse (unclosed constructs) | `partial` + a mark in answers touching that file |
| MySQL unavailable | Schema built from migrations only, `source` marked `migration`; `schema` reports incompleteness |
| Embedding model did not download | Indexing proceeds, the vector layer is marked `unavailable`, `search` works on lexicon, `capability` reports it |
| Evidence conflict | Resolved by §6.1 priority; the conflict is written to `coverage`, the edge marked in `sources` |
| Disk/space | Threshold checked before the build; on shortage — refusal with the computed required volume |

---

## 16. Open questions (resolved by measurement, not guesswork)

| # | Question | How it is resolved |
|---|---|---|
| 1 | The real budget of a full CPG build on 162 K lines of PHP | Measured on the first run; if NFR-2 is exceeded — a decision to narrow the CPG (changed files + their callers only) |
| 2 | `php2cpg` quality on the pilot's real code | Call-coverage metric and comparison with SCIP/PHPStan on 20 manual examples |
| 3 | The threshold for moving from `sqlite-vec` to `qdrant-client` | Measure `search` on 100 / 300 / 500 K chunks |
| 4 | Are saved tree-sitter trees needed for incremental parsing | Measure the gain versus re-parsing on 10% changed files |
| 5 | The optimal `max_chunk_lines` for 9 K-line files | Measure `search` quality on 20 examples |
| 6 | How many `repo_map` levels fit in 2000 tokens | Direct calculation on the pilot via `tiktoken` |

---

*Translated from the original Russian architecture document (v1.0, 2026-10-04). Translation notes mark every place where the document was synced to the shipped code; no other content was altered.*
