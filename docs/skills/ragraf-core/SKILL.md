---
name: ragraf-core
description: Working with the RAGRAF core (<repository>) — the graph-and-vector code index for agents. Use when the work involves the corpus index, read operations, freshness, duplicate collapsing, exclusions, languages, CPG/Joern, telemetry; when the index returns stale facts, capability=degraded, or an empty registry; when an operation must be chosen instead of reading files.
---

# RAGRAF Core

The skill of working with the RAGRAF core: how to get a fact from the index, how to
recognize a fact that must not be trusted, and what to do when the index is broken.
Everything rests on the files of the `<repository>` project; wherever a
`file:line` appears, it was verified by reading, not from memory.

The project rules live in `AGENTS.md` at the project root (read automatically;
*not shipped in the public repository*), the deep passport is
`docs/AGENTS-context.md` (*not shipped*), and the convention checker is
`tools/Invoke-ProjectDoctrine.ps1`.

## 1. When this skill applies

- An answer about the project's code is needed instead of reading files: where
  something is defined, who calls it, what will break, what is in a table, where
  a table is written, where a class is applied.
- The index and the code diverged: a result looks stale, the revision is old, the
  guard reports a divergence.
- The corpus does not answer: `capability=degraded`, an operation refuses, a slice
  is unavailable.
- Exclusions, the language list or corpus settings are changing.
- The development registry shows an empty revision or zeros against a live index.

## 2. The first step of any task — refresh the corpus

Facts come from the index, so a stale index is forbidden:

```powershell
python tools/ragraf.py ensure <project directory>      # find, rebuild, create
python tools/ragraf.py -p config/ragraf.yaml check     # checksum comparison only
```

`ensure` answers with a `status` field: `actual` — the index is current,
`reindexed` — just rebuilt, `created` — there was no corpus and one was created,
`not_a_project` — the directory does not look like a project, `error` — the reason
is in the `reason` field.

A `stale` marker in any operation's answer means the code has moved ahead and the
index's facts are wrong. They must not be worked from.

## 3. Choosing an operation instead of reading files

| Question | Operation |
|---|---|
| «Where is the function or class defined» | `symbol` |
| «Where is it described how something works» | `search` |
| «Who calls it» / «what does it call» | `callers` / `callees` |
| «What will an edit affect» | `impact` |
| «The function body» (only when the text is needed) | `code` |
| «What is in the table» / «who reads and writes it» | `schema` / `table_usage` |
| «Is this write valid» — before writing SQL | `write_check` |
| «Who gets a permission and under which condition» | `policy_trace` |
| «Is there a rule in the styles, and where is the class applied» | `class_usage` |
| «Is the index current» | `freshness`, `index_status` |
| «Overall structure» | `repo_map` |
| «What is registered in the registry» | `catalog` |
| «The file's current content with difference marks» | `live_read` |

Through the shell this is `python tools/ragraf.py -p <config> query <operation> …`,
through the agent's tools — `ragraf_<operation>`. The detailed choice breakdown is
in `reference/operations.md`.

What an operation does not replace: full-text reading where details matter.
`symbol` and `code` return a line range, not the whole file.

## 4. Hard rules

1. **A fact without a `file:line` reference is a guess.** Back every meaningful
   conclusion with a code reference.
2. **Verify a number from a document against the code.** The project has already
   diverged from its own documentation: the code declares 40 tools and 38 facade
   operations, while documents name 18, 16 and 21.
3. **Do not read secret-bearing files** (the list is in `AGENTS.md` §6, *not
   shipped*): only the name, the size and the rule. There is no git history in the
   project — the only protection is not opening them.
4. **Do not run foreign code during reconnaissance.** The project is executed only
   when the task is exactly about that.
5. **Do not treat `selftest` as a gate:** it always returns exit code 0
   (`src/ragraf/cli.py:906`).
6. **`empty result` is exit code 0 with `found=false`**, not an error; exit code 3
   means «the operation cannot run right now», e.g. a slice at
   `capability != "full"` (`src/ragraf/cli.py:336-356`).

## 5. What breaks most often

1. **The registry diverges from the index after `ensure`.** `src/ragraf/ensure.py:308-312`
   used to register the corpus before indexing without recording the run result.
   Measured: an entry with trigger `register` had empty `rev_id`/`capability` and
   `node_count=0` while the index held 1855 nodes. *(Fixed in the shipped code:
   `ensure.py` now calls `record_index_run(..., trigger="ensure:index")`; if you see
   zeros again — the recipe is in `reference/repair.md`.)*
2. **An exclusion edit silently throws out working code.** Matching is
   case-sensitive (`src/ragraf/util.py:72-158`); `**/cache/**` does not catch
   `src/Cache/`. Before extending the list — `python tools/measure_safe_excludes.py`
   (*not shipped; see repair.md §4*).
3. **Duplicate collapsing gets enabled without understanding the price.** Enabling
   it changes the corpus checksum: the freshness guard reindexes the corpus once.
   The choice of the primary copy must be deterministic (`src/ragraf/dedupe.py:165-223`),
   otherwise reindexing never stops.
4. **`capability=degraded` gets mistaken for breakage.** It is an honest mark: the
   CPG was not built, the slice is unavailable, everything else works. The reason is
   always written in `index_status`.
5. **The GPU is treated as a guarantee.** The GPU compute path requires
   `sentence-transformers` and `torch`, which are not declared in the package
   description (`src/ragraf/compute.py:141-146`); the break-even threshold is 1800
   texts for vectors and about 5000 vertices for the graph.

## 6. Order of work

1. Refresh the corpus (`ensure`).
2. Formulate the question in terms of an operation, not a file.
3. Get facts with references and make sure the index is not marked stale.
4. Read a whole file only where an operation does not answer the question.
5. After editing the core:

```powershell
python tools/ragraf.py -p config/ragraf.yaml reindex
python tools/ragraf.py -p config/ragraf.yaml check
pwsh -NoLogo -NoProfile -NonInteractive -File tools/Invoke-ProjectDoctrine.ps1
```

6. The checker knows its baseline: **2 errors, 0 warnings** (11 checks); both
   errors predate the project handover (`AGENTS.md` §10, *not shipped*). A third
   error or a new warning is a finding.

## 7. Skill files

- `reference/operations.md` — how to choose an operation and what it does not show.
- `reference/repair.md` — recipes: stale index, empty registry, `degraded`, a
  looping guard, an exclusion mistake, falling back to the CPU computer.
