# RAGRAF

**A graph-and-vector knowledge base of code for LLM agents.**

[**Website**](https://ragraf.ru) · [**Wiki**](https://github.com/paulmann/RAGRAF/wiki) · [**Docs site**](https://paulmann.github.io/RAGRAF/) · [**Releases**](https://github.com/paulmann/RAGRAF/releases) · [**Discussions**](https://github.com/paulmann/RAGRAF/discussions) · [**Issues**](https://github.com/paulmann/RAGRAF/issues)

![RAGRAF — a graph-and-vector knowledge base of code for LLM agents: 40 MCP tools, verifiable file:line facts, fully offline](assets/social-preview.png)

[![CI](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml/badge.svg)](https://github.com/paulmann/RAGRAF/actions/workflows/ci.yml)
[![Docs site](https://github.com/paulmann/RAGRAF/actions/workflows/pages.yml/badge.svg)](https://paulmann.github.io/RAGRAF/)
[![Release](https://img.shields.io/github/v/release/paulmann/RAGRAF?label=release&sort=semver)](https://github.com/paulmann/RAGRAF/releases)
[![License: PolyForm NC 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![PowerShell 7.6+](https://img.shields.io/badge/PowerShell-7.6%2B-5391FE?logo=powershell&logoColor=white)](https://aka.ms/pwsh)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#installation)
[![MCP](https://img.shields.io/badge/MCP-40%20tools-8A2BE2)](docs/implementation/INTEGRATION.md)
[![Stars](https://img.shields.io/github/stars/paulmann/RAGRAF?style=social&label=Stars)](https://github.com/paulmann/RAGRAF/stargazers)

An assistant that answers an agent's questions about a codebase — *where is it, who calls it, what breaks if I change it, how is it done elsewhere* — with verifiable facts (`file:line`) instead of blind `rg` and full-file reads. Python 3.13+, SQLite (graph + FTS5 + vectors in one place), tree-sitter, local embeddings without torch. **It runs entirely on the machine that owns the code**: no service to run, no cloud index, nothing sent anywhere — a local RAG index over your own repository. An optional property-graph layer (Java + Joern) unlocks slicing; without it the product still works and says so honestly.

> **Documentation status.** Every count in this tree, in `README.ru.md`, in the Russian originals under `docs/ru/` and in the wiki was **re-measured from the sources on 2026-10-16**; the summary is in [By the numbers](#by-the-numbers), and `Test-DocumentedNumbers` in the project checker compares those numbers with the code. Documents that describe the **release-1 scope** keep their own numbers on purpose — `docs/architecture/SPECIFICATION.md` still says 17 operations and states why — while the current surface is 38 operations / 40 MCP tools / 39 CLI query operations.

---

## Contents

- [What this is and why](#what-this-is-and-why)
- [Use cases](#use-cases)
- [Capabilities](#capabilities)
- [By the numbers](#by-the-numbers)
- [How it works](#how-it-works)
- [Installation](#installation)
- [First steps](#first-steps)
- [Requirements and limitations](#requirements-and-limitations)
- [Documentation](#documentation)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## What this is and why

An agent working on a code change spends most of its context on **finding the place**: where a function is defined, who calls it, how something similar is done nearby. The usual path is `rg` plus reading whole files.

RAGRAF replaces that path with an index. The agent asks a question through a fixed operation and gets a fact with a `file:line` reference — not ten candidates to review.

Measured on a real project (PHP portal, 14 navigation tasks; the run was made on the author's machine — see *what the shipped artifacts measure* below):

| Metric | Without the index | With the index |
|---|---|---|
| Tool calls for 14 tasks | 28 (realistically 43) | **14** |
| Average per task | 2.0 / 3.1 | **1.0** |
| Candidates to review | up to 34 files | 1–5 exact |
| Files read in full | 14 (minimum) | **0** |
| Target-file hit on the first query | — | **92%** (12 of 13) |
| Verifiability (facts with `file:line`) | — | **100%** |

The call, hit-rate and verifiability numbers are **measurements** of that run; its raw artifacts are not part of the public tree (tracked as `R-REL-1` in [`ROADMAP.md`](ROADMAP.md)). What does ship is the artifact of the graph metric — [`bench/results/M2_rev-0012.json`](bench/results/M2_rev-0012.json) and [`M2_rev-0025.json`](bench/results/M2_rev-0025.json) measure **impact-analysis completeness** on the same portal: does the graph reach the files that were actually co-changed? Mean recall **0.771 → 0.917** over 20 and 8 commits at depth 3, median recall **1.0**, precision 0.072 → 0.020 — deliberately low, because the metric counts connectivity, not prediction — against a naive baseline of **0.0**.

«Realistically 43» is a modeled correction (the agent reviews up to 5 candidates instead of one), and it is recorded in the artifact itself.

One caveat usually omitted: **the 2–3× win applies to localization tasks** («where is it defined», «who calls it»). In the edit loop «read → change → verify» there is almost no savings in call count — the win there is different: edits do not corrupt the index, and disputed SQL is checked before it runs.

---

## Use cases

| You are doing | Ask RAGRAF for | What comes back |
|---|---|---|
| Onboarding an agent onto a 200k-line repository it has never seen | `repo_map`, then `search` / `symbol` | one fact per question, with `file:line`, instead of a wall of candidates |
| Refactoring a function that is called from a dozen places | `callers`, `impact`, `slice` when the CPG is available | proven links separated from heuristics; a slice that says what it actually covers |
| Renaming or dropping a database column | `write_check` **before** the SQL runs, then `table_usage` | missing columns with nearest-name suggestions; who reads and who writes the table |
| Reviewing someone else's diff | `diff`, `drift_report`, `live_read` | symbols and edges that changed, regressions included, and the current file marked against the index |
| Hunting copies (`Files.php` … `Files7.php`) | `duplicates` | which files are copies, and which copy is *proven* live |
| «How is this done elsewhere in this codebase?» | `search` (BM25 + vectors, hybrid) | ranked fragments from the corpus, no model download needed for the answers |
| Wiring the index into an agent | the **40 MCP tools** `ragraf_*`, or the CLI | the same operations over stdio — see [`docs/implementation/INTEGRATION.md`](docs/implementation/INTEGRATION.md) |

The numbers above are the contract, not marketing: a localization win is measured at **2–3× fewer tool calls**, and the edit loop is measured honestly — see [What this is and why](#what-this-is-and-why).

---

## Capabilities

**Thirty-eight operations** over a repository — a fixed set, with no graph query language:

| Operation | Answers the question |
|---|---|
| `search` | where is this written about (hybrid: BM25 lexicon + vector) |
| `search_multi` | where several strings occur at once; file copies and which one is live |
| `duplicates` | which files are copies of each other, and what proves the main copy is live |
| `entrypoints` | entry points: routes, HTTP handlers, CLI, markup endpoints |
| `dead_code` | files and routines with no references and outside reachability |
| `symbol` | where is the symbol defined: kind, file, lines, signature |
| `references` | where else is it used |
| `callers` / `callees` | who calls it / what does it call |
| `impact` | what will a change affect (proven and heuristic links reported separately) |
| `slice` | structural slice: nodes, edges, lines — without function bodies |
| `code` | symbol bodies (only on explicit request) |
| `repo_map` | repository map within a given character budget |
| `schema` / `table_usage` | what is in a DB table and who reads or writes it |
| `routes` | routes and entry points |
| `policy_trace` | who gets a permission, and under which condition |
| `class_usage` | a styling class: where defined, where applied, what diverges |
| `write_check` | is this write valid (write plan checked against the schema before running) |
| `live_read` | the current file, with differences from the index highlighted |
| `freshness` | is the index current (checksum comparison) |
| `index_status` | revision, coverage, degradation, ledger size |
| `drift_report` / `drift_ack` | ledger of divergences between index and code |
| `catalog` | corpus registry: what is indexed and with which checksums |
| `calibrate` | threshold calibration |
| `usage` | call telemetry and effect estimation |
| `diff` | what changed between revisions: files, symbols, edges, regressions |
| `task_stats` | per-task report: index calls, gate decisions |
| `gate` | gate decision for a tool call: `allow` / `ask` / `deny` |
| `note` / `notes` | record and read a decision note — knowledge between sessions |

The same operations are available as **40 MCP tools** (`ragraf_*`) — to attach to an agent as a server — and through the CLI.

**Sixteen languages** are parsed as trees (graph nodes and edges): PHP, Python, JavaScript, TypeScript, Java, C#, C, C++, Ruby, Lua, Go, Perl, bash, PowerShell, CSS, HTML — thirteen generic profiles plus dedicated PHP, CSS and HTML extractors. Eleven more are attached as text — files are findable by search but yield no nodes: Markdown, SQL, YAML, JSON, TOML, INI, XML, RST, Rust, batch, plain text. 55 file extensions are mapped in total.

---

## By the numbers

Every count below was measured on the published tree on **2026-10-16** — not copied from earlier prose. The two that matter most are gated by `Test-DocumentedNumbers` in [`tools/Invoke-ProjectDoctrine.ps1`](tools/Invoke-ProjectDoctrine.ps1), which compares the documents with the code.

| Surface | Count | Measured from |
|---|---|---|
| Facade operations | **38** | `def op_*` in `src/ragraf/facade.py` |
| MCP tools | **40** — 6 of them the live-database layer | `ragraf_*` names in `src/ragraf/mcp_server.py` |
| MCP resources / prompts | 3 + 1 URI template / 2 | `ragraf://status`, `ragraf://catalog`, `ragraf://corpus/{name}/map`; prompts `ragraf-check`, `ragraf-repair` |
| CLI subcommands / `query` operations | 14 / **39** | `add_parser(…)` and `OPS` in `src/ragraf/cli.py` |
| Languages parsed as trees | **16** | `TREE_LANGS` in `src/ragraf/config.py` |
| Languages attached as text | **11** | every other language in `LANG_BY_EXT` |
| File extensions mapped | 55 | `LANG_BY_EXT` |
| Admin-panel API routes | 32 | `src/ragraf/admin_server.py` |
| Runtime dependencies | 7 (+1 optional extra, `tls`) | `pyproject.toml` |
| Python in `src/` | 50 files · 37 592 lines | the published tree |
| PowerShell — installer, wrappers, checker | 5 files · 3 423 lines | the published tree |
| Markdown | 28 files (20 under `docs/`, 9 of them Russian) + 15 wiki pages | the published tree and the wiki |
| Portable test suites | 6 suites · **164 checks, 0 failures** | each suite run standalone |
| Defect register | 60 findings | [`docs/DEFECTS.md`](docs/DEFECTS.md); the audited revision's summary counts 43 of them by severity |
| Published tree | 136 files · 3.5 MB | the published tree |

Dogfooding, for scale: RAGRAF indexes its own working tree (648 files, 140 741 lines — larger than this published subset) into 3 620 symbols, 46 296 edges and 3 417 chunks in **74 s**, 63 s of which is the optional Joern/CPG stage (`rev-0156`, measured 2026-10-16; `capability=full`, 0 failed files). Full breakdown: [wiki → Benchmarks and Limits](https://github.com/paulmann/RAGRAF/wiki/11-Benchmarks-and-Limits).

---

## How it works

**Three SQLite stores** per corpus, in a `.ragraf/` directory **inside the indexed project**:

| Store | Contents |
|---|---|
| `graph.sqlite` | nodes (class, function, method), edges (calls, inheritance, imports), DB schema |
| `lexvec.sqlite` | FTS5 lexicon (BM25) and vectors in one file; raw chunk text for returning fragments |
| `control.sqlite` | revisions, drift ledger, call telemetry |

**Indexing flow:** `scan` (walk + checksums) → `ast` (tree-sitter: symbols, calls, SQL) → `load` → `resolve` (names) → `schema` (tables from migrations) → `cpg` (interprocedural calls via Joern) → `lexvec` (FTS5 + vectors).

**Three decisions that define the behavior** — each derived from a measured defect:

1. **An immutable index and a ledger of differences instead of «updating on the fly».** Within a revision the index does not change; code edits accumulate in the ledger, and affected facts are marked `stale`. Otherwise facts from different moments in time would mix in a single answer with no way to tell them apart — which is exactly how wrong conclusions are born.
2. **The agent gets no graph query language.** Fixed operations only. Rationale: in LLMxCPG (USENIX Security '25) base models produce 132/1278 and 19/1278 valid CPGQL queries versus 1278/1278 for a fine-tuned one — a free-form language turns into an error generator.
3. **Every fact carries proof.** `path:line`, `provenance` (`stated` — proven, `derived` — heuristic), `index_rev`, `confidence`. An edge without evidence looks credible only because it came «from the database».

**Degradation instead of failure.** A missing code property graph yields `capability=degraded` and an honest refusal from the `slice` operation stating the actual mode; all other operations work. No embedding model — lexical search works. No free port — the server reports the reason. Silent degradation never happens.

---

## Installation

Requires **Python 3.13 or newer**. No administrator rights needed.

### Quick start (one command)

The bootstrap installer fetches the package from GitHub and deploys it in a dialogue:

```powershell
iwr -OutFile install.ps1 https://raw.githubusercontent.com/paulmann/RAGRAF/main/install.ps1
pwsh -NoLogo -NoProfile -File .\install.ps1
```

It asks for the installation directory (default `C:\RAGRAF`), creates the virtual environment and installs dependencies through `scripts/install.ps1`, offers to locate AutoClaw and DeepSeek Harness and to set up the connection, and finally prints how to start the admin panel plus a warning about access to it.

It states plainly what it does not do: **AutoClaw values are shown, not written** — AutoClaw accepts MCP servers only through its own interface, and its store belongs to the product. For **Harness** the installer can write the profile block, with a backup and a parse check afterwards. An existing installation is never overwritten silently, and a rehearsal run that skips dependencies is available: `-SkipDeps`.

### Windows (PowerShell 7.6+)

```powershell
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
pwsh -NoLogo -NoProfile -File scripts/install.ps1
```

The script creates a virtual environment in `~/.ragraf/venv`, installs dependencies, and **verifies that the result is a working environment** — step by step, ending with a summary table. It does not «pretend to install»: every step ends with a check. A full transcript is written to `~/.ragraf/logs/install-<timestamp>.log`, and every external command runs under a timeout.

Useful variants:

```powershell
# install and immediately prepare a corpus for your project
pwsh -File scripts/install.ps1 -Project C:\work\myproject

# same, plus full indexing (downloads the ~240 MB embedding model)
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index

# indexing without vectors: no model download, lexical search works
pwsh -File scripts/install.ps1 -Project C:\work\myproject -Index -NoVectors

# in a TLS-inspecting network (corporate proxy)
pwsh -File scripts/install.ps1 -WithTruststore
```

### Other platforms

One common path, works anywhere with Python 3.13+:

```bash
git clone https://github.com/paulmann/RAGRAF.git
cd RAGRAF
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e .                # or: pip install -r requirements.txt
ragraf --version
```

The product also works without installing the package — via a wrapper that adds `src/` to the path itself:

```bash
python tools/ragraf.py --version
```

> `scripts/install.ps1` is verified on Windows (PowerShell 7.6, Python 3.14). The common `pip` path on Linux and macOS is **not verified** — the product was developed and measured on Windows.

### External layer: Java and Joern (optional)

The interprocedural graph (the `slice` operation) needs JDK 21 and Joern. Without them the product works: the index builds, search and structure are available, and `slice` refuses with a reason.

```powershell
# fetch (official GitHub release + Adoptium API), verify SHA-256, unpack, self-check
pwsh -File scripts/install-joern.ps1 -Download

# or verify/prepare from locally placed artifacts (no network)
pwsh -File scripts/install-joern.ps1
```

Archives go to `.tools/downloads/` — the script is idempotent. The unpacked set takes about 5–6 GB.

---

## First steps

```bash
# 1. Prepare the corpus: find it, create it if missing, rebuild a stale index.
#    A corpus for an unknown directory is created by this same command.
ragraf ensure C:\work\myproject

# 2. Check the environment (report on 11 components).
#    NOTE: the command ALWAYS returns exit code 0 — it is not a gate; read the report.
ragraf -p <config> selftest

# 3. Ask
ragraf -p <config> query search "reindex delay"
ragraf -p <config> query symbol --name corpus_hash
ragraf -p <config> query code --name corpus_hash
ragraf -p <config> query callers --name dedupe
ragraf -p <config> query impact --target Facade --depth 2

# 4. Freshness: did the code change after indexing
ragraf -p <config> check
```

Full command list: `ragraf --help`. Important: **the global flags `--project/-p` and `--json` go BEFORE the subcommand** (`ragraf -p cfg query search ...`), while the subcommand's own flags go after it.

Exit codes: **0** — success (including an empty result with `found=false`), **1** — error, **2** — usage, **3** — the operation cannot run right now (e.g. `slice` while `capability != full`).

### Corpus management

```bash
ragraf init C:\work\myproject         # create the corpus config and print its path
ragraf -p <config> register           # register the project in the registry
ragraf -p <config> index              # full indexing
ragraf -p <config> reindex            # reindex + archive the drift ledger
ragraf -p <config> drift --scan       # register differences WITHOUT touching the index
ragraf -p <config> status             # state: revision, coverage, ledger size
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
        "--project", "/path/to/RAGRAF/config/mycorpus.yaml",
        "--agent", "my-agent",
        "--agent-kind", "external"
      ]
    }
  }
}
```

`--project` is required. `--agent` and `--agent-kind` exist only for per-agent call statistics: without them, calls are logged as `unknown`.

### Admin panel

```bash
python tools/ragraf-admin.py --no-browser
```

Opens on `127.0.0.1:8777` and listens on that address only. Shows corpora and their index states, store sizes, index quality, call statistics. Mutating requests require the access token printed at startup.

---

## Requirements and limitations

| Item | Value |
|---|---|
| Python | **3.13+** (declared in `requires-python`; verified on 3.14) |
| Platform | Windows + PowerShell 7.6 — verified; other OSes — **not verified** |
| Privileges | none required: everything runs from the user profile |
| Disk | corpus index `.ragraf/` — roughly 1 MB per 1–2 source files; the external Java/Joern layer — ~5–6 GB |
| Internet | needed twice: at install time (PyPI) and at the **first indexing with vectors** — the ~240 MB embedding model downloads to `~/.ragraf/models` |
| Java + Joern | optional; without them `capability=degraded` and `slice` is unavailable |
| GPU | optional; accelerates vector encoding only and engages above a break-even threshold |

**About the internet and certificates.** In a TLS-inspecting network (corporate proxy) the model download fails certificate validation. The cure is `truststore`:

```bash
pip install truststore      # or: install.ps1 -WithTruststore
```

On a home network the package is unnecessary. Measured: without it — `certificate verify failed`; with it — 240 MB download in 9.2 s.

**About the model cache.** The cache directory is overridden by `RAGRAF_MODEL_CACHE`. Read operations never download the model — only indexing with vectors does.

---

## Documentation

| Document | Contents |
|---|---|
| [ragraf.ru](https://ragraf.ru) | the public website: features, measured benchmarks, and an emulator of the admin panel |
| [Wiki](https://github.com/paulmann/RAGRAF/wiki) | 15 pages — getting started, operations reference, CLI, MCP, CPG layer, troubleshooting, FAQ |
| [Docs site](https://paulmann.github.io/RAGRAF/) | this documentation set rendered with search (built and deployed by `pages.yml`) |
| [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) | principles, components, store schemas, operation contracts |
| [`docs/architecture/SPECIFICATION.md`](docs/architecture/SPECIFICATION.md) | functional and non-functional requirements, acceptance criteria |
| [`docs/architecture/ACCEPTANCE.md`](docs/architecture/ACCEPTANCE.md) | measurement protocols M1–M7 and the test map |
| [`docs/implementation/INTEGRATION.md`](docs/implementation/INTEGRATION.md) | attaching to an agent: MCP, working rules, the regimen |
| [`docs/implementation/CPG_INTEGRATION.md`](docs/implementation/CPG_INTEGRATION.md) | the code property graph: Joern, JDK, degradation modes |
| [`docs/skills/ragraf-core/SKILL.md`](docs/skills/ragraf-core/SKILL.md) | the skill of working with the index: choosing operations, recovery |
| [`docs/article/RAGRAF_ARTICLE.md`](docs/article/RAGRAF_ARTICLE.md) | overview article: stack, architecture, measurements |
| [`docs/DEFECTS.md`](docs/DEFECTS.md) | the defect register of the published revision — every finding carries `file:line` evidence |
| [`ROADMAP.md`](ROADMAP.md) | planned improvements and development directions |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | how to report defects and propose changes |

> Historical note: the original Russian documentation set is preserved under `docs/ru/` in the source repository history. Some internal documents referenced by the deep-dive docs (research notes, effect reports on the author's private corpora) are intentionally not part of the public tree; references to them are marked *(not shipped)*.

---

## Development

```bash
# tests run as standalone scripts — there is no unified runner yet
python tests/test_lang_routing.py
python tests/test_watch.py

# project convention checker (14 checks)
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1

# refresh the index of RAGRAF itself
python tools/ragraf.py ensure .
```

The shipped checks are only those that run on **synthetic fixtures** and are portable. The rest measured behavior on the author's real corpora (a PHP portal, a C# service, their project configs) and would be meaningless on other code.

What passes right after installation:

| Test | Checks |
|---|---|
| `test_lang_routing.py` | 56 |
| `test_watch.py` | 31 |
| `test_script_extract.py` | 25 |
| `test_threshold.py` | 23 |
| `test_ensure.py` | 17 |
| `test_csharp_records.py` | 12 |

Three checks require things the base installation does not include, and that is **not a packaging defect** but the nature of the check:

* `test_joern_server.py` — needs the external Java + Joern layer (`scripts/install-joern.ps1`);
* `test_graph_algo.py` — needs `torch` (~2.5 GB), which is in neither `pyproject.toml` nor `requirements.txt`: GPU graph algorithms are an optional branch, and without `torch` it honestly degrades to CPU;
* `test_cpg_call_names.py` — needs a corpus config (the `config/` directory is not published); you can run the test by copying `examples/ragraf.example.yaml` into `config/` and pointing it at your own root; without one it refuses to run rather than passing silently.

A tenth file, `test_capability_consistency.py`, reports «not applicable» until a corpus config exists — it validates the declared `capability` against what the graph actually contains. `test_graph_algo.py` without `torch` still runs **7 checks** and names the **17 properties it could not check**, rather than printing a green line over untested code.

The project checker is equally explicit about this published subset: of its **14 checks**, 11 pass here and 3 report files that are deliberately absent from the public tree (the corpus config, `tests/test_dedupe.py`, `tests/test_text_from_index.py`) — tracked as `R-REL-1` in [`ROADMAP.md`](ROADMAP.md).

`selftest` is **not a gate**: it prints a report on 11 components and always returns exit code 0. A non-zero code is not provided for, so a «green selftest» proves nothing by itself.

---

## Contributing

Useful reports: a reproducible defect with the command and its output; a document diverging from the code; a case of degradation the product did not name. One thing is welcome above all: a fact with a file-and-line reference. See [CONTRIBUTING.md](CONTRIBUTING.md); questions, ideas and measurements on your own corpus go to [Discussions](https://github.com/paulmann/RAGRAF/discussions). Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).

The project is developed for concrete tasks, so architecture proposals are discussed more readily than large patches without a prior conversation. Security problems are reported privately, never as a public issue — see [SECURITY.md](SECURITY.md).

If this saved you a context window, a star helps the next agent find it.

---

## License

**[PolyForm Noncommercial License 1.0.0](LICENSE)** — free for any noncommercial purpose: personal projects, study, research, experiment and testing, and use by charitable, educational, public-research, public-safety, health and environmental organizations. Commercial use needs a separate license from the copyright holder, who keeps every right not expressly granted. [CHANGELOG.md](CHANGELOG.md) records the transition from MIT on 2026-10-15.

> GitHub's licence badge for this repository reads «Other»: PolyForm is not in the catalogue GitHub detects, so the badge cannot be trusted here. `LICENSE` and `pyproject.toml` (`license = "PolyForm-Noncommercial-1.0.0"`) are the authoritative sources.

External dependencies keep their own licenses: `tree-sitter` (MIT), `sqlite-vec` (MIT/Apache-2.0), `fastembed` (Apache-2.0), `PyYAML` (MIT), `tiktoken` (MIT), `ijson` (BSD). Java and Joern, installed separately, are distributed under Apache-2.0 and are not part of the distribution.
