# RAGRAF

**Graph-and-vector knowledge base over source code for LLM agents.**

[![CI](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml/badge.svg)](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulmann/RAGRAF?label=release&sort=semver)](https://github.com/paulmann/RAGRAF/releases)
[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-informational)](pyproject.toml)

![RAGRAF — graph-and-vector knowledge base over source code for LLM agents: 61 MCP tools, verifiable facts as file:line, fully local](assets/social-preview.png)

[**Quick start**](#first-steps) · [**Documentation**](#documentation) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Changelog**](CHANGELOG.md) · [**Русская версия**](README.ru.md)

> **Documentation status.** Every number below was measured on 26–27.10.2026 against version 0.4.0; the sizes of the RAGRAF index are compared with the active revision record by `Test-DocumentedNumbers`, and a figure that belongs to a past revision fails the release gate rather than passing as today's measurement.

An assistant that answers an agent's questions about a codebase — *where is it, who calls it, what breaks if I change it, how is it done elsewhere* — with verifiable facts (`file:line`) instead of blind `rg` and full-file reads. Python 3.13+, SQLite (graph + FTS5 + vectors in one place), tree-sitter, local embeddings without torch. An optional property-graph layer (Java + Joern) unlocks slicing; without it the product still works and says so.

---

## Contents

- [What it is and why](#what-it-is-and-why)
- [Capabilities](#capabilities)
- [How it works](#how-it-works)
- [Installation](#installation)
- [First steps](#first-steps)
- [Requirements and limits](#requirements-and-limits)
- [Documentation](#documentation)
- [Development](#development)
- [Contributing](#contributing)
- [The project in numbers and the measured test bed](#the-project-in-numbers-and-the-measured-test-bed)
- [License](#license)

---

## What it is and why

An agent working on a code change spends most of its context on **finding the place**: where a function is defined, who calls it, how something similar is done nearby. The usual route is `rg` plus reading whole files.

RAGRAF replaces that route with an index. The agent asks through a fixed operation and gets a fact with a `file:line` reference instead of ten candidates to inspect.

Measured on a real project (a PHP portal, 14 navigation tasks, `bench/results/`):

| Metric | Without the index | With the index |
|---|---|---|
| Tool calls for 14 tasks | 28 (43 realistically) | **14** |
| Average per task | 2.0 / 3.1 | **1.0** |
| Candidates to inspect | up to 34 files | 1–5 exact |
| Files read in full | 14 (the minimum) | **0** |
| Target file hit on the first query | — | **92%** (12 of 13) |
| Verifiability (facts carrying `file:line`) | — | **100%** |

Call counts, hit rates and verifiability are **measurements** (the run artefacts are in `bench/results/`). "43 realistically" is a model correction — the agent inspects up to five candidates instead of one — and it is recorded inside the artefact itself.

One caveat is usually left out: **the 2–3× gain applies to localisation tasks** ("where is it defined", "who calls it"). In the edit loop — read, change, verify — call counts barely move; the gain there is elsewhere: edits do not corrupt the index, and questionable SQL is checked before it runs.

---

## Capabilities

**54 operations** over a repository — a fixed set, with no graph query language:

| Operation | Answers the question |
|---|---|
| `search` | where this is written about (hybrid: BM25 lexis + vectors) |
| `search_multi` | where several strings occur at once; which files are copies and which copy is live |
| `duplicates` | which files are copies of each other and what proves which one is active |
| `entrypoints` | entry points: routes, HTTP handlers, CLI, markup endpoints |
| `dead_code` | files and routines with no references, outside reachability |
| `symbol` | where a symbol is defined: kind, file, lines, signature |
| `references` | where else it is used |
| `callers` / `callees` | who calls it / what it calls |
| `impact` | what a change touches (stated and heuristic edges kept apart) |
| `slice` | structural slice: nodes, edges, lines — without function bodies |
| `code` | bodies of symbols (only on an explicit request) |
| `repo_map` | the repository map within a given character budget |
| `schema` / `table_usage` | what a database table holds and who reads or writes it |
| `routes` | routes and entry points |
| `policy_trace` | who gets a permission, and under which condition |
| `class_usage` | a styling class: where defined, where used, where the two disagree |
| `write_check` | whether a write is possible (plan checked against the schema before it runs) |
| `live_read` | the current file, with differences from the index marked |
| `freshness` | whether the index is current (checksum comparison) |
| `index_status` | revision, coverage, degradation, journal size |
| `drift_report` / `drift_ack` | the journal of divergences between index and code |
| `catalog` | the corpus registry: what is indexed, with which checksums |
| `route` | where to go for an answer: which corpus answers, and why (no quoting) |
| `neighbors` | whether a ready implementation exists in neighbouring corpora of the same stack |
| `calibrate` | threshold calibration |
| `usage` | access telemetry and the effect estimate |
| `diff` | what changed between revisions: files, symbols, edges, regressions (P2-1) |
| `task_stats` | a task report: index calls, gate decisions (§3.4) |
| `gate` | the gate decision for one tool call: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | record and read a decision note — knowledge across sessions (P2-3) |
| `quality_suite` / `quality_diff` / `quality_prune` | the quality loop (pro edition): run index checks, compare two runs, thin the run journal with a copy and a confirmation |
| `shared_index_status` / `shared_index_publish` / `shared_index_query` | the team index (pro edition): publish a corpus for network reads, admit a client, query the published index |
| `broker_issue` / `broker_rotate` | the secret broker (pro edition): issue a secret on request — a lease with a term, a custodian and a reason — and rotate the value on a plan (leases are revoked by the rotation) |

Two further modes are not operations but a shape of answer: **`answer=true`** returns a phrased answer with references instead of a structural result (P2-2), and `brief=true` returns a short answer shell. `usage` reports the effect metrics (`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

The same operations are exposed as **61 MCP tools** (`ragraf_*`) — for attachment to an agent as a server — and through the CLI. One group of tools is the live database layer: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`. Two operations of the closed tier (enterprise edition) are declared as tools: `ragraf_license_offline` (offline licence: request, issue, acceptance, transfer) and `ragraf_attest_bundle` (delivery attestation: a signed evidence bundle and its verification). The quality loop (pro edition) is `ragraf_quality_suite` (an index check suite plus a run record), `ragraf_quality_diff` (divergences between two runs) and `ragraf_quality_prune` (thinning the run journal: a dry run by default, deletion only with confirmation and after a copy of the journal). Three operations of the shared index (also pro) are outside the MCP set: they manage publication and the network channel rather than index reads, and run as `python -m ragraf.shared_index` (see `docs/03_implementation/SHARED-INDEX.md`). The secret broker (pro) is represented by two tools — `ragraf_broker_issue` (lease: term, custodian, reason, one-time redemption) and `ragraf_broker_rotate` (plan state, scheduling, rotation with lease revocation); lease state lives in `catalog/broker.json`, and the storage schema does not change.
The twenty-fourth one is `use_project`: choosing a corpus when one server serves several projects. `context` answers which corpus is selected and on what grounds — without it, answers about a foreign project were indistinguishable from answers about your own.

**Twenty-nine languages** are parsed into a syntax tree (graph nodes and edges). Sixteen have their node map described by a profile: PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS, HTML. The remaining thirteen (Rust, Kotlin, Swift, Scala, Dart, R, F#, OCaml, Solidity, Fortran, MATLAB, Elm, Lisp) take it from the grammar's own tags query: their node names do not match the familiar ones, and a profile per language would mean inventing names. Every language is checked on a sample file — at least one symbol found (`tests/test_new_languages.py`).

Forty more are attached as plain text: search finds the files, but they yield no nodes. These are Julia, Elixir, Erlang, Haskell, Clojure, Groovy, Visual Basic, Pascal/Delphi, Objective-C, Zig, Nim, Crystal, Tcl, Prolog, Ada, Verilog, VHDL, assembly, HCL/Terraform, Dockerfile, Makefile, CMake, SCSS, Less, Vue, Svelte, GraphQL, Protobuf, SQL, batch, Markdown, RST, YAML, JSON, TOML, INI, XML, plain text and the `document` language.

**Documents are indexed in full, old versions included.** Word: `.docx`, `.docm`, `.dotx` and `.doc` (Word 97 through a dedicated OLE parser, no LibreOffice). Excel: `.xlsx`, `.xlsm`, `.xltx` and `.xls` (BIFF8). PowerPoint: `.pptx`, `.pptm`, `.ppsx`, `.potx` and `.ppt`. Plus PDF, OpenDocument (`.odt`, `.ods`, `.odp`, `.odg`), EPUB, RTF, `.eml` and `.msg` mail, and WPS Office files. Formats without a parser (Pages, OneNote, CHM, DjVu, `.xlsb`) do not pretend to be empty documents — they are reported with a reason.

What gets indexed is chosen in the panel: **Settings → the "Formats" tab** — 84 formats grouped by kind (languages, documents, data), a filter by name, extension and parse mode, "all"/"none" buttons and a counter of enabled entries. All formats are enabled by default, and `*` means "all, including those added later". Disabling a format changes the corpus contents and triggers a reindex at the next freshness check.

---

## How it works

**Three SQLite stores** per corpus, in a `.ragraf/` directory **inside the indexed project**:

| Store | Contents |
|---|---|
| `graph.sqlite` | nodes (class, function, method), edges (calls, inheritance, imports), database schema |
| `lexvec.sqlite` | FTS5 lexis (BM25) and vectors in one file; raw chunk text for excerpts |
| `control.sqlite` | revisions, divergence journal, access telemetry |

**Indexing pipeline:** `scan` (walk and checksums) → `ast` (tree-sitter: symbols, calls, SQL) → `load` → `resolve` (names) → `schema` (tables from migrations) → `cpg` (interprocedural calls through Joern) → `lexvec` (FTS5 + vectors).

**Three decisions that define the behaviour** — each derived from a measured defect:

1. **An immutable index and a divergence journal instead of updating as you go.** Inside a revision the index does not change; code edits accumulate in the journal, and affected facts are marked `stale`. Otherwise facts from different points in time would mix in one answer with nothing to tell them apart — which is exactly how wrong conclusions are born.
2. **The agent gets no graph query language.** Fixed operations only. The basis: in LLMxCPG (USENIX Security '25) base models produce 132/1278 and 19/1278 valid CPGQL queries against 1278/1278 for a trained one, so a free-form language turns into an error generator.
3. **Every fact carries evidence.** `path:line`, `provenance` (`stated` — proven, `derived` — heuristic), `index_rev`, `confidence`. An edge without evidence looks trustworthy only because it came "from the database".

**Degradation instead of failure.** Without the property-graph layer you get `capability=degraded` and a clear refusal of `slice` naming the actual mode; other operations keep working. Without an embedding model, lexical search works. Without a free port, the server states the reason. Silent degradation does not exist.

---

## Installation

**Python 3.13 or newer** is required. No administrator rights are needed.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

The script creates a virtual environment in `~/.ragraf/venv`, installs the dependencies and **checks that a working environment actually came out** — step by step, with a summary table. It does not pretend to have installed anything: every step ends with a verification.

Useful variants:

```powershell
# install and immediately prepare a corpus for your own project
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# the same plus a full index (downloads the ~240 MB embedding model)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# index without vectors: no model download, lexical search still works
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# behind TLS inspection (corporate proxy)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Other platforms

One path that works wherever Python 3.13+ exists:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # or: pip install -r requirements.txt
ragraf --version
```

The product also runs without installing the package, through a wrapper that puts `src/` on the path itself:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` is verified on Windows (PowerShell 7.6, Python 3.14.7). The `pip` path on Linux and macOS is **not verified** — the product was developed and measured on Windows.

### External layer: Java and Joern (optional)

The interprocedural graph (the `slice` operation) needs JDK 21 and Joern. Without them the product still works: the index is built, search and structure are available, and `slice` refuses with a reason.

```powershell
pwsh -File scripts/install-joern.ps1
```

The archive lands in `.tools/downloads/` — the script is idempotent. The unpacked toolchain takes about 5 GB.

---

## First steps

```bash
# 1. Prepare the corpus: find it, build it if missing, rebuild a stale index.
#    For an unknown directory the corpus is created by the same command.
ragraf ensure C:\work\myproject

# 2. Check the environment (a report over 11 components).
#    NOTE: this command ALWAYS returns exit code 0 — it is not a gate, read the report.
ragraf -p <config> selftest

# 3. Ask
ragraf -p <config> query search "reindex latency"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Freshness: has the code changed since the last index
ragraf -p <config> check
```

The full command list is `ragraf --help`. Note: **the global options `--project/-p` and `--json` come BEFORE the subcommand** (`ragraf -p cfg query search ...`); a subcommand's own options follow it.

### Corpus management

```bash
ragraf init C:\work\myproject         # create a corpus configuration and print its path
ragraf -p <config> register           # register the project in the registry
ragraf -p <config> index              # full index
ragraf -p <config> reindex            # reindex and archive the divergence journal
ragraf -p <config> drift --scan       # record divergences WITHOUT changing the index
ragraf -p <config> status             # state: revision, coverage, journal size
ragraf -p <config> usage              # telemetry: calls, facts, effect estimate
```

### Attaching to an agent as an MCP server

```json
{
  "mcpServers": {
    "ragraf": {
      "command": "python",
      "args": [
        "/path/to/RAGRAF/tools/ragraf-mcp.py",
        "--project", "/path/to/RAGRAF/examples/mycorpus.yaml",
        "--agent", "my-agent",
        "--agent-kind", "external"
      ]
    }
  }
}
```

`--project` is mandatory. `--agent` and `--agent-kind` are only needed for per-agent access statistics: without them the calls are recorded as `unknown`.

### Settings panel

```bash
python tools/ragraf-admin.py --no-browser
```

It opens on `127.0.0.1:8777` and listens on that address only. It shows corpora and the state of their indexes, storage sizes, index quality and access statistics. Mutating requests require an access token printed at start-up.

The sources page — `http://127.0.0.1:8777/sources`: allowed roots for the "I am you" mode, their state, adding, scanning, enabling and deletion. Changes require the same token; file contents are never read, and secret-bearing files are visible only as a count and a rule. A network root is accepted only as a UNC path or a mounted path with `--network`; URL connectors (`ssh://`, `s3://`, `webdav://`) are not implemented and are rejected with a reason.

The value page — `http://127.0.0.1:8777/value`: 30-day calls, facts, token and line estimates, summed across corpora. Estimates are labelled; paths and queries are not there.

---

## Requirements and limits

| Item | Value |
|---|---|
| Python | **3.13+** (declared in `requires-python`; verified on 3.14) |
| Platform | Windows + PowerShell 7.6 — verified; other OSes — **not verified** |
| Privileges | none: everything runs from the user profile |
| Disk | a corpus index `.ragraf/` — about 1 MB per 1–2 source files; the Java/Joern layer — about 5 GB |
| Network | needed twice: at install (PyPI) and at the **first index with vectors** — the ~240 MB embedding model is downloaded into `~/.ragraf/models` |
| Java + Joern | optional; without them `capability=degraded` and `slice` is unavailable |
| GPU | optional; it accelerates vector encoding only and switches on at the break-even threshold |

**On network and certificates.** Behind TLS inspection (corporate proxy) the model download fails the certificate check. Installing `truststore` fixes it:

```bash
pip install truststore      # or: install.ps1 -WithTruststore
```

On a home network the package is not needed. Measured: without it — `certificate verify failed`; with it — 240 MB in 9.2 s.

**On the model cache.** The cache directory is overridden by `RAGRAF_MODEL_CACHE`. Read operations never download the model — only indexing with vectors does.

---

## Documentation

| Document | Contents |
|---|---|
| `docs/02_architecture/ARCHITECTURE.md` | principles, components, storage schema, operation contract |
| `docs/02_architecture/TZ.md` | functional and non-functional requirements, acceptance criteria |
| `docs/03_implementation/INTEGRATION.md` | attaching to an agent: MCP, working rules, policy |
| `docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md` | setup in HARNESS and AutoClaw, the full capability catalogue |
| `docs/03_implementation/HARNESS_INTEGRATION.md` | DeepSeek Harness: MCP, skill, rules, gate |
| `docs/03_implementation/AUTOCLAW_INTEGRATION.md` | AutoClaw: MCP, skill, rules, rollback |
| `docs/03_implementation/CPG_INTEGRATION.md` | property graph: Joern, JDK, degradation modes |
| `docs/skills/ragraf-core/SKILL.md` | the skill for working with the index: operation choice, recovery |
| `docs/05_article/RAGRAF_ARTICLE.md` | overview article: stack, architecture, measurements |
| `AGENTS.md` | the development rules of this project; the baseline conventions checker |

---

## Development

```bash
# tests run as separate scripts — there is no single runner
python tests/test_lang_routing.py
python tests/test_watch.py

# the project conventions checker (14 checks)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# refreshing RAGRAF's own index
python tools/ragraf.py ensure .
```

The delivery ships only the checks that run on **synthetic fixtures** and travel well. The rest measure behaviour on the author's real corpora (a PHP portal, a C# service, their project configurations) and mean nothing on other code.

What passes right after installation:

| Test | Checks |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Three checks need something the base installation lacks, and that is **not a delivery defect** but the nature of the check:

* `test_joern_server.py` — needs the Java + Joern layer (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — needs `torch` (about 2.5 GB), which is in neither `pyproject.toml` nor `requirements.txt`: GPU graph algorithms are an optional branch, and without `torch` it degrades to the CPU honestly;
* `test_cpg_call_names.py` — needs a corpus configuration (the `config/` directory is not published); it can be run by copying `examples/ragraf.example.yaml` into `config/` and pointing it at your own root.

`selftest` is **not a gate**: it prints a report over 11 components and always exits with code 0. No other code is provided for it, so a green `selftest` proves nothing by itself.

---

## Contributing

Useful reports: a reproducible defect with its command and output; a document that diverges from the code; a degradation the product did not name. One thing is welcome above all: a fact with a file and a line.

The project is built for concrete tasks, so architecture proposals get a hearing sooner than large patches without a conversation first.

---

## The project in numbers and the measured test bed

Published figures (checked on 26.10.2026; index sizes from the `rev-0405` record of 27.10.2026): **54 operations** on the facade, **61 MCP tools**, **21** CLI subcommands, **29** tree-parsed languages and **40** text-attached ones (including `document`), **84** data formats in the registry (61 languages, 9 document families, 10 data kinds, 4 without a parser); the RAGRAF index itself — 863 files, 5 178 nodes and 68 122 edges (the snapshot is compared with the active revision record by `Test-DocumentedNumbers`: figures from a past revision raise a warning naming its `rev_id` and date, and at publication that is a refusal — `tools/release/gate_release.py`; the live figures come from `python tools/ragraf.py -p config/ragraf.yaml status`).

**The test bed, depersonalised** (project names, paths and domains removed). The figures in this section are a **snapshot of 16.10.2026**, not the current state: at that moment the registry held **28 corpora, 25 indexed** — 10 with the CPG layer, 15 in `degraded` mode. For 16 of them the revision record was read the same day: **7 527 files, 94 528 symbols, 791 871 edges, 86 790 chunks**.

The current registry size is kept apart from the snapshot on purpose: it changes with every attached project, and putting it into the table below would pass yesterday's measurement off as today's. It is named in its own line and compared with `catalog/catalog.json` — **by the registry on 27.10.2026: 42 corpora, 33 indexed**. A checker watches this figure: if it diverges from the registry, that is a finding, not a stale line.

| Corpus | Kind | Files | Symbols | Edges | Mode |
|---|---|---|---|---|---|
| C-02 | web application in PHP | 2 722 | 32 761 | 355 192 | degraded |
| C-04 | service / desktop in C# | 1 274 | 20 331 | 98 605 | degraded |
| C-05 | web application in PHP | 928 | 17 381 | 99 029 | full |
| C-18 | automation set in PowerShell | 81 | 324 | 4 214 | degraded |
| C-24 | Markdown + bash (the smallest) | 18 | 184 | 1 515 | degraded |

The full 16-row table, the depersonalisation rules and the counting caveat live in [the wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits). The caveat: the corpus registry and a revision record give different totals for the same revision (8 721 vs 4 498 symbols, 41 201 vs 56 106 edges), so the figures are taken from the revision record. The private map from `C-*` labels to projects stays outside the repository: it belongs to the author and is not part of the delivery.

---

## License

**[Business Source License 1.1](LICENSE)** — the source is open, and the licence permits any use, including commercial activity and production use inside an organisation. One thing is forbidden: offering the product or its derivatives to third parties as a hosted or managed service whose main value is RAGRAF's functionality. Four years after a version is released it moves to Apache-2.0.

Sources of truth — one question per file, no paraphrase:

| Question | File |
|---|---|
| The licence text and its parameters | [`LICENSE`](LICENSE) |
| Third parties and their licences | [`NOTICE`](NOTICE) |
| Terms of contribution | [`CLA.md`](CLA.md) |
| Support: what is promised and what is not | [`SUPPORT.md`](SUPPORT.md) |
| What is allowed, what is not, licence history | [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md) |

> BSL 1.1 is **not an open-source licence**: the OSI does not approve it, so the correct words are "source-available", "the source is open". The licence badge on GitHub shows "Other": BSL is absent from the catalogue it recognises.

A case the grant does not cover needs a separate commercial licence from the rights holder: all rights not expressly granted remain with them. Rights granted earlier are not revoked — the licence history is in [`docs/03_implementation/LICENSING.md`](docs/03_implementation/LICENSING.md).

External dependencies keep their own licences: the list with measured versions is in [`NOTICE`](NOTICE). Java and Joern are not part of the delivery and are installed separately.

### Secrets vault (owner decision 2026-10-24)

The rule used to be "values do not exist and cannot exist" — a register of places only. The owner changed it: values are needed, but they may live **only** in `catalog/` (outside the corpus), **only** encrypted, and are issued to an agent by right with an audit record. The "do not index" rule stands: `catalog/**` is excluded from the corpus, so a value reaches neither the index nor search.

- types: password, token, API key, private key, certificate, connection string,
  SSH key, webhook, note — that is, not "passwords only";
- encryption: PyNaCl `SecretBox` (XSalsa20-Poly1305), the per-entry key is derived from the
  master key with the entry identifier as salt, and the entry context is embedded in the
  ciphertext — swapping rows between entries is rejected by the authenticity check;
- per-agent rights: `all` (the default — available to everyone), `listed` (only the named
  agents), `owner_only` (only the agent that stored it); plus a global allowed-agent list
  in the settings;
- audit: every issue and every refusal, with an append-only journal at the database level;
- settings: the `secrets_*` section of the panel — enabling the vault, capturing encountered
  secrets, default visibility, requiring a reason for an issue, value length limit, the
  allowed-agent list, expiry warning.

CLI: `python tools/ragraf.py secrets list | show | reveal | set | rm | grant |
revoke | check | rotate | export | import | policy | audit`. The `set` value comes
from stdin, an environment variable or a file — never from a command-line argument.
MCP: `ragraf_secret_list/show/reveal/set/grant/revoke/delete/policy`.
