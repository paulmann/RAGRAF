# RAGRAF Operations: Choice and Boundaries

Reference for the `ragraf-core` skill. The task is to choose an operation instead of
reading files — and to know what an operation does not show.

## 1. The full set

The facade declares 38 operations of the form `op_*` (`src/ragraf/facade.py`). The
agent tool surface is 40 tools with the `ragraf_` prefix (`src/ragraf/mcp_server.py`).
The difference is not an error, and it is not one-to-one. Five tools have no facade
operation of their own — `context`, `ensure`, `task`, `usage`, `use_project`: they
manage the corpus and the session rather than operate on a repository. Three facade
operations are not exposed as tools: `calibrate`, `gate`, `task_set`.
(Counts measured against the sources on 2026-10-16.)

## 2. By question type

| Question | Operation | Shows | Does not show |
|---|---|---|---|
| Where is a symbol defined | `symbol` | file, lines, kind, signature, parsed parameters | the function body |
| Where else is it used | `references` | all sites with references | execution order |
| Who calls it | `callers` | callers with evidence | calls through a string or a data reference |
| What does it call | `callees` | callees | same |
| What will an edit affect | `impact` | places reachable in the graph | branch conflicts and runtime |
| Structural slice | `slice` | context without bodies, only on a full CPG build | nothing at `capability=degraded` |
| A symbol's body | `code` | the exact text | the meaning of the places calling it |
| Repository map | `repo_map` | files and volumes within a budget | the links between them |
| Search by meaning and words | `search` | chunks with paths and lines | link evidence — this is search, not the graph |
| What is in a table | `schema` | columns and relations parsed from migrations | the actual state of a live database |
| Who reads or writes a table | `table_usage` | read and write sites | database access rights |
| Is this write valid | `write_check` | the write plan checked against the schema before execution | value types and constraints |
| Who gets a permission and why | `policy_trace` | decision branches, refusal points | the user's actual role |
| Styling classes | `class_usage` | whether a rule exists, where the class is applied | the final look in the browser, the cascade |
| Routes and handlers | `routes` | HTTP routes and their handlers | behavior behind the handler |
| Index currency | `freshness` | checksum divergence, reindexing when needed | the quality of the facts themselves |
| Index state | `index_status` | revision, parse completeness, coverage, stages | nothing beyond the state |
| Divergence ledger | `drift_report` | what changed after the revision | the bodies of the changes |
| Live reading | `live_read` | the file's current content with difference marks | links and the graph |
| Call statistics | `usage` | calls, facts, savings | the meaning of the facts |
| Development registry | `catalog` | what is registered and with which numbers | the index contents |

## 3. Choice rules

1. **Navigation is an operation; content is reading.** «Where» is answered by an
   operation; «how exactly it works» requires reading the fragment the operation
   pointed to.
2. **A write plan is always `write_check`.** It is the only check that catches a
   nonexistent column before the code runs.
3. **Permission decisions are always `policy_trace`.** Reading a large branch set
   with the eyes is useless: an unknown action left allowed by default is visible
   only statically.
4. **Before editing markup — `class_usage`.** A missing rule in the styles is not an
   error: the page renders with browser defaults and works.
5. **`slice` only on a full build.** Otherwise the operation refuses with code 3 —
   that is expected behavior, not breakage.
6. **`live_read` — when the file definitely changed after the revision.** Otherwise
   the index suffices, and it is cheaper.

## 4. How to read an answer

Mandatory envelope fields: `op` (operation), `index_rev` (which revision answered),
`capability` (`full` or `degraded`), `drift` (whether unclosed divergences exist),
`provenance` (where the data came from: index, live read, or mixed), `limits`,
`result`, `stale_warnings`, `found`. Additionally there may be `error` and
`freshness`.

- `found=false` at exit code 0 is «not found», not a failure.
- Non-empty `stale_warnings` means the fact may have been touched by edits after the
  revision; such a fact must not be served as current without the mark.
- `provenance=live` means the data was obtained by reading the file, not from the
  index: its evidence is the file itself, not the revision.

## 5. What the project does not have

- There is no graph query language. Fixed operations only; text resembling a graph
  query is rejected with a call error.
- There is no standard dependency version-check command, although
  `requirements.txt` promises one.
- There is no unified test runner: there is no pytest configuration, and the files
  in the `tests` directory run individually.
