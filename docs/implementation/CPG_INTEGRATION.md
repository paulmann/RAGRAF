# CPG Layer: Joern Integration

This document records **exactly what was verified** about Joern as applied to our corpus, and which conclusions follow for the pipeline. It was compiled from primary sources (the `joernio/joern` source tree, official documentation), not from memory: every fact carries a confidence level.

---

## 1. The key discovery that changes the preconditions

**php2cpg does not contain its own PHP parser.** It invokes an external `php` with a bundled `php-parser.phar`.

Confirmed from the sources:

- `joern-cli/frontends/php2cpg/README.md`: *«A PHP to CPG converter based on `php-parse`. Requirements: PHP (>=7.0)»*;
- `PhpParser.scala`, method `phpParseCommand`: `Seq("php", "--php-ini", phpIniPath, phpParserPath) ++ Seq("--with-recovery", "--resolve-names", "--json-dump") ++ filenames`;
- `Php2Cpg.createCpg` → `isPhpVersionSupported` runs `ExternalCommand.run(Seq("php", "--version"))` and requires **≥ 7.1.0**; otherwise `Failure("php not found or version not supported")`.

**Consequence for the pipeline:** without `php` in `PATH` the frontend does not degrade — it **refuses**. That is why `cpg.py` has a separate `find_php()` check, and the process environment is assembled so the directory containing `php` comes first in `PATH` — the frontend invokes literally `php`; a full path to `php.exe` does not work.

> **Translation note:** the original stated «on this machine: PHP 8.5 found (`C:\tools\php85\php.exe`)» — machine-local state of the author's environment, not a product requirement. The product requirement is: a `php` ≥ 7.1 executable discoverable in `PATH` (or via `RAGRAF_PHP_BIN`/config — see roadmap R-CORE-2; the shipped code currently also probes two hardcoded `C:/tools/...` paths, a known defect).

---

## 2. Verified facts about structure and launch

| Fact | Source | Level |
|---|---|---|
| Frontends and their scripts live in `joern-cli/frontends/<name>/` | `joern-cli/build.sbt`: `frontendMappings("php2cpg", …)` → `ref -> s"frontends/$frontendName/$name"` | confirmed |
| The `joern`, `joern-parse`, `joern-export`, `joern-slice` utilities are in `joern-cli/bin/` | sbt-native-packager mechanics (`JavaAppPackaging`) | inference, not a quote |
| The language is set with `--language PHP` (case-insensitive) | frontends table; `buildLanguageList` — «Available languages (case insensitive)» | confirmed |
| `--frontend-args` passes arguments to the frontend verbatim | `CpgBasedTool.scala`: `ARGS_DELIMITER = "--frontend-args"` | confirmed |
| `--exclude <path>` excludes files (path relative to the input) | docs.joern.io/frontends/x2cpg | confirmed *(but see §8 item 2: php2cpg rejects it)* |
| Script mode requires `@main def exec(...)` and `--param k=v` | docs.joern.io/interpreter | confirmed *(unusable on JDK 21 — see §8 item 3)* |
| A query result is written to a file with the `#>` operator | docs.joern.io/shell | confirmed |
| `PHP_PARSER_BIN` overrides the parser path | `PhpParser.scala`: `private val PhpParserBinEnvVar = "PHP_PARSER_BIN"` | confirmed |
| The frontend always passes `--with-recovery` | `phpParseCommand` | confirmed |

**Not verified (requires a run on the unpacked distribution):**
- the exact contents of `joern-cli/bin` and the presence of `.bat` twins;
- whether `joern` has a single-command flag (`-c`/`--command`) — the documentation lacks it, so the pipeline does not rely on it;
- the behavior of `-J-Xmx` specifically in `joern-parse.bat`.

---

## 3. How to extract the data (verified against the exporter's code)

`cpg.bin` is a flatgraph binary; **it cannot be read from Python directly**. An export is needed.

| Need | `--repr` | Compatible formats |
|---|---|---|
| CFG / DDG / CDG / PDG | `cfg`/`ddg`/`cdg`/`pdg` | **`dot` only** |
| The whole graph with all edge types | `all` | `graphml`, `graphson`, `neo4jcsv`, `dot` |
| Subgraph per method | `cpg` | `graphml`, `graphson`, `neo4jcsv`, `dot` |

From `JoernExport.scala`: in `exportWithFlatgraphFormat` there are branches for exactly `All` and `Cpg`; for the rest — `throw new NotImplementedError(...)`. That is, **the combination `--repr=pdg --format=graphml` does not work**.

**Decision taken:** the primary path is **CPGQL scripts** producing JSON. This gives exact control over what we extract (we need calls and methods, not the whole graph) and requires no GraphML/DOT parsing. The scripts live in `src/ragraf/cpg.py` (`QUERY_CALLS`, `QUERY_METHODS`, `QUERY_TYPES`).

> **Translation note:** on JDK 21 the script mode proved broken (§8 item 3) and the shipped pipeline uses `joern-export` with `--repr=all` in `neo4jcsv` format, parsed by `src/ragraf/joern_export.py`. The CPGQL constants remain for reference/compatibility.

Additionally: `joern-export` **itself** builds the data-flow overlays (`addDataFlowOverlayIfNonExistent` calls `OssDataFlow`), so a separate overlay run for the PDG is unnecessary.

---

## 4. php2cpg limitations critical for our corpus

### 4.1. Syntax is limited to PHP 8.2

The bundled parser is `nikic/PHP-Parser` branch 4.x:

- `MODULE.bazel`: `PHP_PARSER_VERSION = "4.15.10"`;
- fork README: *«This is a PHP 5.2 to PHP 8.2 parser written in PHP»*.

The pilot is **PHP 8.4**. New constructs (property hooks, asymmetric visibility) are absent from the 4.x grammar → syntax errors.

**Mitigating factor:** the frontend always passes `--with-recovery`, so an incomplete AST is expected rather than a crash. How incomplete — verified on the run.

**What to do:** count this in the completeness metric. The report gets a separate line about files php2cpg failed to parse (in the logs these are `Failed to process '<file>'` — a warning under which the file silently drops out; such drops must be caught).

### 4.2. No composer.json → incomplete name resolution

`buildFiles` looks **only** for `composer.json`: `SourceFiles.determine(config.inputPath, Set(".json"), …).filter(_.endsWith("composer.json"))`. The pilot has no composer.json (framework-less PHP, autoloading via `spl_autoload_register`).

**Mitigating factor:** the frontend walks the code **twice** — `SymbolSummaryPass` collects symbols across all namespaces, then `AstCreationPass` builds the AST with that summary (*«parses the code twice… This helps resolve symbols during the latter parse»*). Intra-project links partially resolve.

### 4.3. JDK 21 is outside the tested range

docs.joern.io/installation: *«JDK 19 … newer JDK versions might work, but have not been properly tested»*. We run JDK 21.

A separate warning in the slicing documentation: *«Class path issues related to Circe are known to rear its head on Java versions later than 17»* — concerns `joern-slice` specifically.

**What to do:** if `joern-slice` falls over on JDK 21, the alternative is slicing by our own means through CPGQL (CFG/DDG traversal), or installing JDK 19. This is resolved on the first run.

### 4.4. There is no incremental CPG update

Neither `joern-parse`, nor `joern-export`, nor the documentation describes an incremental rebuild; `joern-scan --overwrite` is described as full regeneration.

**Consequence:** the CPG rebuilds entirely on every reindexing. This agrees with the project principles (the index is immutable within a revision; reindexing is a separate act) but means the CPG stage is the most expensive in the run, and its time must be measured.

---

## 5. Linking the CPG to our graph

| CPG node | Key properties | Join key with our nodes |
|---|---|---|
| `FILE` | `name` | source path |
| `METHOD` | `name`, `fullName`, `filename`, `lineNumber`, `lineNumberEnd`, `isExternal` | `(filename, lineNumber, name)` |
| `TYPE_DECL` | `name`, `fullName`, `filename`, `lineNumber` | classes, interfaces, traits, enums |
| `CALL` | `name`, `code`, `methodFullName`, `dispatchType`, `lineNumber`, `columnNumber` | **`methodFullName` — the thing tree-sitter's index does not have** |
| `IDENTIFIER`, `LITERAL`, `LOCAL`, `PARAMETER` | `name`, `code`, `lineNumber`, `typeFullName` | variables and values |

`dispatchType` distinguishes `STATIC_DISPATCH` from `DYNAMIC_DISPATCH` — a ready-made marker for separating proven from heuristic links.

**Implemented name resolution** (`Indexer._make_fqn_resolver`): exact full-name match → method-name-and-file match → **refusal**. The third outcome is principled: in framework-less PHP, short names (`get`, `run`) belong to dozens of classes, and binding «by name» would produce exactly the invented links that architecture principle P3 protects against.

**Not verified:** php-parser's line-numbering base (1-based or 0-based). Checked by comparison on the first run; on an off-by-one, the join key needs a correction.

---

## 6. What remained to be resolved by running

| Question | How it is checked |
|---|---|
| The presence and exact names of executables in the distribution | `scripts/install-joern.ps1` (does this automatically) |
| Whether `joern-parse --language PHP` works on our corpus | the first run of the CPG stage |
| How many files php2cpg fails to parse because of PHP 8.4 | comparison against the Joern log and the tree-sitter result (we have 0 parse errors) |
| CPG build time on 112 K lines of PHP | timing of the `cpg` stage |
| Memory consumption and the needed `-Xmx` | timing + observation |
| Whether the CPG's `lineNumber` matches our lines | manual comparison of 20 methods |
| Whether `joern-slice` works on JDK 21 | a separate run |

---

## 7. State at the time of writing

> **Translation note:** this is a historical snapshot of the author's machine (October 2026), kept for provenance. None of these components ships with RAGRAF; install them via `scripts/install-joern.ps1`.

| Component | State (author's machine) |
|---|---|
| JDK 21 (Temurin 21.0.12.1) | ✅ installed in `.tools/jdk`, launch-verified |
| PHP CLI 8.5 | ✅ found — the php2cpg precondition is met |
| Joern 4.0.99 | ✅ installed in `.tools/joern-cli` (1633 MB), `php` confirmed in `--list-languages` |
| The CPG layer in the pipeline (`src/ragraf/cpg.py`) | ✅ works: `capability=full` on the pilot |
| Export parsing (`src/ragraf/joern_export.py`) | ✅ neo4jcsv via ijson |
| The `cpg` stage in the indexer | ✅ merging confirmed calls into our graph |
| Install script | ✅ `scripts/install-joern.ps1` |

---

## 8. What only a run revealed (facts absent from the documentation)

This is the most valuable part: Joern's documentation describes none of these points, and without them the CPG layer does not work.

| # | Discovered | How it manifested | Resolution |
|---|---|---|---|
| 1 | `php2cpg` **requires an external `php` in PATH** (it has no parser of its own) | without PHP the frontend does not start at all | `find_php()` + putting the php directory first in PATH |
| 2 | **php2cpg does not accept `--exclude`** | `Unknown option --exclude` → `RuntimeException: unable to parse XTypeStubsParserConfig` | filtering service paths AFTER the export (`is_noise_file`) |
| 3 | **Script mode (`--script`) does not work on JDK 21** | compilation error: no predef script (`importCpg`, `cpg` not found) | moved entirely to `joern-export` |
| 4 | **GraphSON export dies on memory even at 18 GB** | `OutOfMemoryError: Required array length 2147483640 + 8 is too large` | `neo4jcsv` format: streaming CSVs, 223 MB, 27 s |
| 5 | **`--repr=cpg` creates a file per method and crashes** | 731 files, 739 MB, `FileSystemException` | `--repr=all` + filtering while parsing |
| 6 | **`JAVA_OPTS` does not reach the JVM** | `OutOfMemoryError` at any value of the variable | the limit is passed as the argument `-J-Xmx18g` (the form without `=`) |
| 7 | **Joern paths do not match ours** | leading dot and backslashes; matching always refused | `normalize_joern_path` |
| 8 | **Joern writes methods with `->`, not `::`** | `App\Core\Config->get` matched nothing | `normalize_joern_name`; result: 411 links by full name |
| 9 | **AST traversal without a method boundary captures everything** | the `<global>` stub exists in every file and contains the whole file: 179 330 of 183 357 calls got it as owner | stop traversal at nested METHODs |
| 10 | **In the Joern export the vertex array is called `vertices`, not `nodes`** | parsing yielded 0 vertices against 83 542 parsed edges | key `@value.vertices.item`; properties live in `properties` under UPPER-CASE names |
| 11 | **Most CALLs are language operators** | 50 K of 183 K are `<operator>.assignment` | `_is_noise_call`; 17 822 real calls remain |
| 12 | **PHP builtins must be classified separately** | 13 008 of 17 822 «unresolved» were `array_push`, `is_array`, `Iterator.next` | `_is_builtin_target`: 7 819 recognized as normal; the honest remainder — 4 985 |

**Lesson for the project.** Of the 12 items, 7 led to a **silent** loss of data: the code did not crash, but the result turned out zero or distorted. Such defects are caught only by cross-checking independent paths — exactly as happened with CPGQL (worked in theory, not in practice) and with the `<global>` filter (formally the traversal is correct; in fact almost all data landed on one owner).

---

## 9. Result on the pilot

| Indicator | Value |
|---|---|
| `capability` | **full** (CPG connected) |
| Full indexing time with CPG | 71 s (of which CPG — 64 s) |
| CPG build | 18.6 MB, ~50 s for 323 files |
| Export | 223.8 MB, 132 files, ~27 s |
| Meaningful vertices extracted | 193 442 |
| Edges extracted | 3 348 516 (incl. 444 464 CFG, 930 567 data-flow) |
| Calls after filtering | 17 822 of 183 357 |
| **Links confirmed in the graph** | **603** (411 by full name, 192 by name and file) |
| PHP builtins recognized | 7 819 |
| Honestly unresolved | 4 985 |
| `slice` | **works** (was unavailable in degraded mode) |
| **Effect on M2** | **recall 0.771 → 0.858**; median 1.000; zero-recall cases 0 (was 1); full connectivity in 13 of 20 (was 11) |

---

*Translated from the original Russian integration study. Section 8 is the operational core of this document and is translated verbatim in structure; machine-local state is annotated rather than removed.*
