# RAGRAF

<!-- md-i18n:switcher -->
**Read this in:** English · [Deutsch](README.de.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Русский](README.ru.md)

**A graph-vector knowledge base of code for LLM agents.**

[**Website**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Documentation (Pages)**](https://paulmann.github.io/RAGRAF/) · [**Releases**](https://github.com/paulmann/RAGRAF/releases) · [**Discussions**](https://github.com/paulmann/RAGRAF/discussions)

![RAGRAF — a graph-vector knowledge base of code for LLM agents: 40 MCP tools, verifiable facts with file:line references, fully local](assets/social-preview.png)

An assistant that answers an agent's questions about a codebase — *where is it, who calls it, what breaks if I change it, how is it done elsewhere* — with verifiable facts (`file:line`) instead of blind `rg` and full-file reads. Python 3.13+, SQLite (graph + FTS5 + vectors in one file), tree-sitter, local embeddings without torch. **Everything runs on the machine where the code lives**: no service to spin up, the index never goes to the cloud — a local RAG index over your repository. An optional property-graph layer (Java + Joern) unlocks slicing; without it the product still works and says so honestly.

---

## Contents

- [What it is and why](#what-it-is-and-why)
- [Scenarios](#scenarios)
- [Capabilities](#capabilities)
- [Project in numbers](#project-in-numbers)
- [How it works](#how-it-works)
- [Installation](#installation)
- [First steps](#first-steps)
- [Requirements and limits](#requirements-and-limits)
- [Documentation](#documentation)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## What it is and why

An agent working on a code change spends most of its context on **locating the place**: where a function is defined, who calls it, how something similar is done nearby. The usual path is `rg` plus reading whole files.

RAGRAF replaces that path with an index. The agent asks a question through a fixed operation and gets a fact with a `file:line` reference instead of ten candidates to review.

Measured on a real project (PHP portal, 14 navigation tasks; the run was made on the author's machine — see "what exactly is in the artefacts" below):

| Metric | Without the index | With the index |
|---|---|---|
| Tool calls for 14 tasks | 28 (43 realistically) | **14** |
| Average per task | 2.0 / 3.1 | **1.0** |
| Candidates to review | up to 34 files | 1–5 exact |
| Files read in full | 14 (minimum) | **0** |
| Target-file hit on the first query | — | **92%** (12 of 13) |
| Verifiability (facts with a `file:line` reference) | — | **100%** |

The call counts, hit rate and verifiability are **measurements** of that run; its raw artefacts are not part of the published tree (item `R-REL-1` in [`ROADMAP.md`](ROADMAP.md)). What the distribution does contain is a graph-metric artefact: [`bench/results/M2_rev-0012.json`](bench/results/M2_rev-0012.json) and [`M2_rev-0025.json`](bench/results/M2_rev-0025.json) measure **the completeness of impact analysis** on the same portal — whether the graph reaches the files that actually changed together. Mean recall **0.771 → 0.917** over 20 and 8 commits at depth 3, median recall **1.0**, precision 0.072 → 0.020 (low on purpose: the metric measures connectivity, not prediction) against a naive baseline of **0.0**.

"43 realistically" is a model correction (the agent reviews up to 5 candidates instead of one), and it is recorded in the artefact itself.

An important caveat that is usually left out: **the 2–3× gain applies to localisation tasks** ("where is it defined", "who calls it"). In the edit loop "read → change → check" there is almost no saving in call count — the gain there is different: edits do not break the index, and questionable SQL is checked before it runs.

---

## Scenarios

| What you are doing | What to ask RAGRAF | What comes back |
|---|---|---|
| Onboarding an agent into an unfamiliar repository of 200 thousand lines | `repo_map`, then `search` / `symbol` | one fact per question, with a `file:line` reference, instead of a wall of candidates |
| Refactoring a function that is called from a dozen places | `callers`, `impact`, `slice` when CPG is available | proven relations separately from heuristics; a slice that says exactly what it covers |
| Renaming or dropping a database column | `write_check` **before** running the SQL, then `table_usage` | missing columns with their nearest names; who reads and who writes the table |
| Reviewing someone else's diff | `diff`, `drift_report`, `live_read` | changed symbols and edges, regressions included, plus the current file with drift marks |
| Looking for copies (`Files.php` … `Files7.php`) | `duplicates` | which files are copies and which copy is **proven** to be the live one |
| "How is this done elsewhere in the code?" | `search` (BM25 + vectors, hybrid) | ranked corpus fragments; the answers themselves need no embedding model |
| Connecting the index to an agent | **40 MCP tools** `ragraf_*` or the CLI | the same operations over stdio — see [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) |

---

## Capabilities

**38 operations** over a repository — a fixed set, with no graph query language:

| Operation | Answers the question |
|---|---|
| `search` | where is this written about (hybrid: BM25 lexis + vector) |
| `search_multi` | where several strings occur at once; file copies and which of them is the live one |
| `duplicates` | which files are copies of each other and what proves the main one is active |
| `entrypoints` | entry points: routes, HTTP handlers, CLI, markup endpoints |
| `dead_code` | files and subroutines without references and out of reachability |
| `symbol` | where a symbol is defined: kind, file, lines, signature |
| `references` | where else it is used |
| `callers` / `callees` | who calls it / what it calls |
| `impact` | what a change will affect (proven and heuristic relations separately) |
| `slice` | structural slice: nodes, edges, lines — without function bodies |
| `code` | symbol bodies (only on explicit request) |
| `repo_map` | a map of the repository within a given character budget |
| `schema` / `table_usage` | what is in a database table and who reads or writes it |
| `routes` | routes and entry points |
| `policy_trace` | who gets a right, and under which condition |
| `class_usage` | a styling class: where defined, where used, what disagrees |
| `write_check` | whether this write is possible (reconciling the write plan with the schema before it runs) |
| `live_read` | the current file with its differences from the index highlighted |
| `freshness` | whether the index is current (checksum reconciliation) |
| `index_status` | revision, coverage, degradation, journal size |
| `drift_report` / `drift_ack` | journal of divergences between the index and the code |
| `catalog` | corpus register: what is indexed and with which checksums |
| `calibrate` | threshold calibration |
| `usage` | request telemetry and effect estimate |
| `diff` | what changed between revisions: files, symbols, edges, regressions (P2-1) |
| `task_stats` | task report: index requests, gate decisions (§3.4) |
| `gate` | gate decision for a tool call: `allow` / `ask` / `deny` (§3.2) |
| `note` / `notes` | record and read a note about a decision — knowledge between sessions (P2-3) |

Two more modes are not operations but a response shape: **`answer=true`** returns
a formulated answer with references instead of a structured result (P2-2), and
`brief=true` returns a short response envelope. `usage` exposes effect metrics
(`saved_calls`, `hit_rate`, `avg_ms`, `misses`, `stale_served`).

The same operations are available as **40 MCP tools** (`ragraf_*`) — for connecting to an agent as a server — and through the CLI. A separate group of tools is the live database layer: `ragraf_db_schema`, `ragraf_db_table`, `ragraf_db_relations`, `ragraf_db_sample`, `ragraf_db_drift`, `ragraf_db_cross`.
The twenty-fourth is `use_project`: choosing a corpus when one server serves several projects. `context` answers which corpus was selected and on what grounds: without it, answers about someone else's project were indistinguishable from answers about your own.

**Sixteen languages** are parsed by tree (graph nodes and edges): PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS, HTML — thirteen shared profiles plus separate extractors for PHP, CSS and HTML. Eleven more are connected as text — files are found by search but give no nodes: Markdown, SQL, YAML, JSON, TOML, INI, XML, RST, Rust, batch, plain text. 55 extensions are mapped in total.

---

## Project in numbers

All numbers below are measured over the published tree on **16.10.2026** — not copied from earlier texts. Two of them are guarded by `Test-DocumentedNumbers` in [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1): it reconciles the documents with the code.

| Surface | How many | Measured from |
|---|---|---|
| Facade operations | **38** | `def op_*` in `src/ragraf/facade.py` |
| MCP tools | **40** — 6 of them are the live database layer | `ragraf_*` names in `src/ragraf/mcp_server.py` |
| MCP resources / prompts | 3 + 1 URI template / 2 | `ragraf://status`, `ragraf://catalog`, `ragraf://corpus/{name}/map`; prompts `ragraf-check`, `ragraf-repair` |
| CLI subcommands / `query` operations | 14 / **39** | `add_parser(…)` and `OPS` in `src/ragraf/cli.py` |
| Languages parsed by tree | **16** | `TREE_LANGS` in `src/ragraf/config.py` |
| Languages connected as text | **11** | the remaining `LANG_BY_EXT` values |
| Extensions mapped | 55 | `LANG_BY_EXT` |
| Admin panel API routes | 32 | `src/ragraf/admin_server.py` |
| Runtime dependencies | 7 (+1 optional `tls` set) | `pyproject.toml` |
| Python in `src/` | 50 files · 37,592 lines | published tree |
| PowerShell — installer, wrappers, checker | 5 files · 3,423 lines | published tree |
| Markdown | 28 files (20 in `docs/`, 9 of them Russian) + 15 wiki pages | published tree and wiki |
| Portable test suites | 6 suites · **164 checks, 0 failures** | each suite run separately |
| Defect register | 61 findings | [`docs/DEFECTS.md`](docs/DEFECTS.md); 43 of them are counted in the audit summary by severity |
| Published tree | 136 files · 3.5 MB | published tree |

For scale: RAGRAF indexes its own working tree (648 files, 140,741 lines — more than this published subset) into 3,620 symbols, 46,296 edges and 3,417 chunks in **74 s**, of which 63 s are the optional Joern/CPG stage (`rev-0156`, measured 16.10.2026; `capability=full`, 0 files with errors). The detailed breakdown is in [wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits).

**Measured polygon, anonymised.** The register holds **28 corpora, 25 indexed** — 10 with the CPG layer, 15 in `degraded` mode. For 16 of them the revision record was read on 16.10.2026: **7,527 files, 94,528 symbols, 791,871 edges, 86,790 chunks**. Project names, paths and domains are hidden on purpose: the labels are ordered by size, and only the author's private map resolves them.

| Corpus | Type | Files | Symbols | Edges | Mode |
|---|---|---|---|---|---|
| C-02 | PHP web application | 2,722 | 32,761 | 355,192 | degraded |
| C-04 | C# service / desktop | 1,274 | 20,331 | 98,605 | degraded |
| C-05 | PHP web application | 928 | 17,381 | 99,029 | full |
| C-18 | PowerShell automation set | 81 | 324 | 4,214 | degraded |
| C-24 | Markdown + bash (the smallest) | 18 | 184 | 1,515 | degraded |

The full 16-row table, the anonymisation rules and one caveat on counting are in [wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits). The caveat matters when quoting numbers: the corpus register and the revision record give **different** totals for the same revision (measured on one corpus: 8,721 vs 4,498 symbols, 41,201 vs 56,106 edges), so all numbers above are taken from the **revision record** — the one the response envelope returns.

---

## How it works

**Three SQLite stores** per corpus, in a `.ragraf/` directory **inside the indexed project**:

| Store | What is inside |
|---|---|
| `graph.sqlite` | nodes (class, function, method), edges (calls, inheritance, imports), database schema |
| `lexvec.sqlite` | FTS5 lexis (BM25) and vectors in one file; raw chunk text for fragment output |
| `control.sqlite` | revisions, drift journal, request telemetry |

**Indexing pipeline:** `scan` (walk and checksums) → `ast` (tree-sitter: symbols, calls, SQL) → `load` → `resolve` (names) → `schema` (tables from migrations) → `cpg` (interprocedural calls via Joern) → `lexvec` (FTS5 + vectors).

**Three decisions that define the behaviour** — each derived from a measured defect:

1. **An immutable index and a difference journal instead of "updating as you go".** Within a revision the index does not change; code edits accumulate in the journal, and the affected facts are marked `stale`. Otherwise facts from different points in time would mix in one answer without distinction — that is exactly how wrong conclusions are born.
2. **The agent is not given a graph query language.** Only fixed operations. The rationale: in LLMxCPG (USENIX Security '25) base models produce 132/1278 and 19/1278 valid CPGQL queries against 1278/1278 for a trained one — that is, a free-form language turns into an error generator.
3. **Every fact carries evidence.** `path:line`, `provenance` (`stated` — proven, `derived` — heuristic), `index_rev`, `confidence`. An edge without evidence looks trustworthy only because it came "from the database".

**Degradation instead of failure.** A missing property-graph layer yields `capability=degraded` and a clear refusal of the `slice` operation stating the actual mode; the other operations work. No embedding model — lexical search works. No free port — the server reports the reason. Silent degradation never happens.

---

## Installation

**Python 3.13 or newer** is required. Administrator rights are not needed.

### Quick install in one command

The installer downloads the package from GitHub and deploys it in a dialogue:

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

It asks for the installation directory (default `C:\RAGRAF`), creates a virtual environment and installs the dependencies through `scripts/install.ps1`, offers to look for AutoClaw and DeepSeek Harness and set up the connection, and at the end prints instructions for starting the control panel and a warning about access to it.

The installer states plainly what it does not do: **it shows the values for AutoClaw but does not write them** — AutoClaw accepts MCP servers only through its own interface, and its store belongs to the product. For **Harness** the installer can write a profile block: with a backup and a subsequent parse check. A found installation is never overwritten silently, and a rehearsal without dependencies is available through the `-SkipDeps` switch.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

The script creates a virtual environment in `~/.ragraf/venv`, installs the dependencies and **verifies that the result is a working environment** — step by step, with a final table. It does not "pretend to have installed": every step ends with a check.

Useful variants:

```powershell
# install and immediately prepare a corpus for your project
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# the same plus full indexing (downloads the ~240 MB embedding model)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# indexing without vectors: the model is not downloaded, lexical search still works
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# on a network with TLS inspection (corporate proxy)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Other platforms

One common path, works anywhere Python 3.13+ is available:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # or: pip install -r requirements.txt
ragraf --version
```

The product also works without installing the package — through a wrapper that adds `src/` to the path itself:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` has been verified on Windows (PowerShell 7.6, Python 3.14.7). The common `pip` path on Linux and macOS has **not been verified** — the product was developed and measured on Windows.

### External layer: Java and Joern (optional)

The interprocedural graph (the `slice` operation) needs JDK 21 and Joern. Without them the product works: the index is built, search and structure are available, `slice` refuses with a reason.

```powershell
pwsh -File scripts/install-joern.ps1
```

The archive goes to `.tools/downloads/` — the script is idempotent. The unpacked kit takes about 5 GB.

---

## First steps

```bash
# 1. Prepare the corpus: find it, create what is missing, rebuild a lagging index.
#    The corpus for an unfamiliar directory is created by this same command.
ragraf ensure C:\work\myproject

# 2. Check the environment (a report over 11 components).
#    WARNING: the command ALWAYS returns exit code 0 — it is not a gate, read the report.
ragraf -p <config> selftest

# 3. Ask
ragraf -p <config> query search "reindex lag"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Freshness: has the code changed since indexing
ragraf -p <config> check
```

The full command list is `ragraf --help`. Note: **the global switches `--project/-p` and `--json` go BEFORE the subcommand** (`ragraf -p cfg query search ...`), while the subcommand's own switches go after it.

### Corpus management

```bash
ragraf init C:\work\myproject         # create the corpus configuration and print its path
ragraf -p <config> register           # register the project in the registry
ragraf -p <config> index              # full indexing
ragraf -p <config> reindex            # reindexing + archiving of the drift journal
ragraf -p <config> drift --scan       # record differences WITHOUT changing the index
ragraf -p <config> status             # state: revision, coverage, journal size
ragraf -p <config> usage              # telemetry: requests, facts, effect estimate
```

### Connecting to an agent as an MCP server

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

`--project` is required. `--agent` and `--agent-kind` are needed only for per-agent request statistics: without them calls land in the journal as `unknown`.

### Settings panel

```bash
python tools/ragraf-admin.py --no-browser
```

It opens on `127.0.0.1:8777` and listens on that address only. It shows corpora and the state of their indexes, store sizes, index quality and request statistics. Modifying requests require an access token, which is printed at startup.

---

## Requirements and limits

| Item | Value |
|---|---|
| Python | **3.13+** (declared by `requires-python`; verified on 3.14) |
| Platform | Windows + PowerShell 7.6 — verified; other OSes — **not verified** |
| Rights | not required: everything runs from the user's home directory |
| Disk | the corpus index `.ragraf/` — about 1 MB per 1–2 source files; the external Java/Joern layer — ~5 GB |
| Internet | needed twice: at installation (PyPI) and at the **first indexing with vectors** — a ~240 MB embedding model is downloaded to `~/.ragraf/models` |
| Java + Joern | optional; without them `capability=degraded` and `slice` is unavailable |
| GPU | optional; speeds up vector encoding only and switches on at the break-even threshold |

**On the internet and certificates.** On a network with TLS inspection (corporate proxy) the model download fails on certificate verification. The fix is installing `truststore`:

```bash
pip install truststore      # or: install.ps1 -WithTruststore
```

On a home network the package is not needed. Measured: without it — `certificate verify failed`, with it — 240 MB downloaded in 9.2 s.

**On the model cache.** The cache directory is overridden by the `RAGRAF_MODEL_CACHE` variable. Read operations never download the model — only indexing with vectors does.

---

## Documentation

| Document | What is inside |
|---|---|
| [ragraf.ru](https://ragraf.ru) | the public site: capabilities, measured benchmarks, an admin panel emulator |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 pages: getting started, operation reference, CLI, MCP, the CPG layer, diagnostics, FAQ |
| [Documentation (Pages)](https://paulmann.github.io/RAGRAF/) | this documentation set with search — built and published by the `pages.yml` pipeline |
| [`docs/ru/02_architecture/ARCHITECTURE.md`](docs/ru/02_architecture/ARCHITECTURE.md) | principles, components, store layout, operation contract |
| [`docs/ru/02_architecture/TZ.md`](docs/ru/02_architecture/TZ.md) | functional and non-functional requirements, acceptance criteria |
| [`docs/ru/03_implementation/INTEGRATION.md`](docs/ru/03_implementation/INTEGRATION.md) | connecting to an agent: MCP, working rules, policy |
| [`docs/ru/03_implementation/CPG_INTEGRATION.md`](docs/ru/03_implementation/CPG_INTEGRATION.md) | property graph: Joern, JDK, degradation modes |
| [`docs/ru/skills/ragraf-core/SKILL.md`](docs/ru/skills/ragraf-core/SKILL.md) | the skill for working with the index: choosing an operation, recovery |
| [`docs/ru/05_article/RAGRAF_ARTICLE.md`](docs/ru/05_article/RAGRAF_ARTICLE.md) | overview article: stack, architecture, measurements |
| [`docs/DEFECTS.md`](docs/DEFECTS.md) | the register of found defects, each finding with `file:line` evidence |
| [`ROADMAP.md`](ROADMAP.md) | planned improvements with stable identifiers (`R-*`) |

---

## Development

```bash
# tests run as separate scripts — there is no single runner
python tests/test_lang_routing.py
python tests/test_watch.py

# the project convention checker (14 checks)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# refreshing RAGRAF's own index
python tools/ragraf.py ensure .
```

The distribution contains only those checks that run on **synthetic
fixtures** and are portable. The rest measure behaviour on the author's real
corpora (a PHP portal, a C# service, their project configurations) and make no
sense on other code.

What passes right after installation:

| Test | Checks |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Three checks need what a base installation does not have, and that is **not a
packaging defect** but the nature of the check:

* `test_joern_server.py` — needs the external Java + Joern layer (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — needs `torch` (about 2.5 GB), which is in neither `pyproject.toml`
  nor `requirements.txt`: graph algorithms on the GPU are an optional branch,
  and without `torch` it degrades honestly to the CPU;
* `test_cpg_call_names.py` — needs a corpus config (the `config/` directory is not published);
  the test can be run by copying `examples/ragraf.example.yaml` into `config/` and
  pointing it at your own root; without a config it refuses to start instead of "passing" silently.

The tenth file, `test_capability_consistency.py`, answers "not applicable" while there is no corpus config:
it checks that the declared `capability` matches the content of the graph. `test_graph_algo.py`
without `torch` still runs **7 checks** and names **17 properties it could not verify**,
instead of a green line over unverified code.

The project checker is equally honest about this published subset: of its **14 checks**, 11 pass
here, and 3 report files that are deliberately absent from the public tree (the corpus config,
`tests/test_dedupe.py`, `tests/test_text_from_index.py`) — that is item `R-REL-1` in
[`ROADMAP.md`](ROADMAP.md).

`selftest` is **not a gate**: it prints a report over 11 components and always
returns exit code 0. A non-zero code is not provided for it, so a "green
selftest" proves nothing by itself.

---

## Contributing

Useful reports: a reproducible defect with the command and the output; a document that disagrees with the code; a case of degradation the product did not name. One thing is welcomed: a fact with a file and line reference. See [CONTRIBUTING.md](CONTRIBUTING.md); questions, ideas and measurements on your own corpus go to [Discussions](https://github.com/paulmann/RAGRAF/discussions). Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

The project is developed for specific tasks, so architecture proposals are discussed more readily than large patches without a prior conversation. Security issues are reported privately, not in a public issue — see [SECURITY.md](SECURITY.md).

If this saved you a context window — a star helps the next agent find the project.

---

## License

**[Business Source License 1.1](LICENSE)** (BUSL-1.1, Community). The terms are in the [`LICENSE`](LICENSE) file; the machine-readable expression is `license = "BUSL-1.1"` in `pyproject.toml`. The history of license changes (MIT → PolyForm Noncommercial → BUSL-1.1) is recorded in [CHANGELOG.md](CHANGELOG.md).

> The license badge on GitHub cannot be trusted: not every expression is recognised, and BUSL-1.1 may show as "Other". The sources of truth are the `LICENSE` file and the `license` field in `pyproject.toml`.

External dependencies keep their own licenses: `tree-sitter` (MIT), `sqlite-vec` (MIT/Apache-2.0), `fastembed` (Apache-2.0), `PyYAML` (MIT), `tiktoken` (MIT), `ijson` (BSD). Java and Joern, installed separately, are distributed under Apache-2.0 and are not part of the distribution.
