# RAGRAF Community — local code index for Claude Code

RAGRAF indexes your repository on your own machine: a symbol tree, a call graph, full-text
search, vectors and a database schema parsed from migrations. Every answer comes with an
evidence link — `path:line` — instead of a plausible retelling, and your code never leaves
the machine.

Ask it *where something is defined*, *who calls it*, *what breaks if I change it* — the
three questions that otherwise cost a dozen file reads.

## What you get in Claude Code

| Component | What it does |
|---|---|
| MCP server `ragraf` | tools `mcp__plugin_ragraf_ragraf__ragraf_*`: search, symbols, callers, impact, code, schema, write-check, freshness |
| Hook `SessionStart` | adds one line to the session: corpus, revision, whether the index is fresh |
| Hook `UserPromptSubmit` | the first prompt of a session becomes the task label for usage statistics |
| Hook `PreToolUse` | before file search and reads, reminds the model to ask the index first (mode `warn` by default) |
| Hook `Stop` | appends a per-task usage line (calls, facts, share with `file:line`) |
| Skill `ragraf-core` | when and how to use the index, what to do when it is stale |
| `/ragraf-status` | corpus state as a table |
| `/ragraf-task <name>` | name the current task so index calls are counted in it |

Nothing is always-on except the skill descriptions: measured with `claude plugin details`
— about 120 tokens per session. Tool schemas are resolved at runtime and not counted.

## Install

```text
/plugin marketplace add paulmann/RAGRAF
/plugin install ragraf@ragraf
```

Then, from a shell in the project you want indexed:

```powershell
ragraf init . --name my-project   # once per project
ragraf index -p <config>          # build the index (first run takes a while)
```

Requirements:

* **RAGRAF itself is installed** — either a checkout (`RAGRAF_HOME`, or a known location such
  as `C:\AutoClaw\RAGRAF`, `~/RAGRAF`, `/opt/ragraf`) or the Python package (`pip install ragraf`).
  The plugin ships the glue, not the indexer: it locates your installation at start-up and
  reports what it found (`ragraf_launcher.py where`).
* **`python` on PATH** — Claude Code runs the launcher with it; the launcher then prefers the
  project's `.venv` (or `RAGRAF_PYTHON`) so that dependencies resolve.

## Editions

The plugin is the **Community** edition: free, complete for a single developer, and limited
by licence file, not by a different package.

| Included in Community | Requires a licence |
|---|---|
| search, symbol tree, call graph, schema from migrations, live database layer, local audit | document graph over documents (entities and relations), OCR of scans, index sharing between machines, secret broker token for teams, quality loop |
| MCP, hooks, skill, commands as described above | SSO, SIEM export, offline licence and attestation (Enterprise) |

Paid tiers are described in `EULA.md` (shipped with the plugin) and on the project page.
Without a licence file the server runs as Community and refuses gated operations with a
message naming the edition they belong to — it does not degrade silently.

## Privacy and security

* The index and the server are **local**: stdio MCP subprocess, no listening port, no
  outbound traffic during search. Embeddings are computed locally.
* **No telemetry by default.** Usage statistics stay in the local index database; the
  `/ragraf-status` page reads them from there.
* **The hooks do not read your code.** They open the index, read the session id and the tool
  name, and write one JSON line back to Claude Code. The gate can only *advise* or *block a
  call*; it cannot rewrite a command.
* **No secrets are collected.** Values are not indexed; they live in RAGRAF's local vault and
  are handed out only with a reason recorded in an audit log.

## Boundaries

* Hooks need a **checkout** of RAGRAF (they run `tools/harness_gate.py`). With a package-only
  installation the server, skill and commands work, and hooks report on stderr why they are
  skipped — a session is never broken by that.
* The hook never reindexes: a full rebuild takes minutes. Freshness is reported, and the model
  is asked to call `ensure` first.
* The plugin does not grant itself permissions. Allow `mcp__plugin_ragraf_ragraf__*` in
  `permissions.allow` if you do not want a prompt per tool call.

## Support and licence

* Issues and documentation: <https://github.com/paulmann/RAGRAF>
* Licence: Business Source License 1.1 — see `LICENSE`. Notice: `NOTICE`. Paid tiers:
  `EULA.md`.

The plugin folder is assembled from the project sources: `SKILL.md` is written for the plugin
tool prefix, the reference files are byte-identical copies of the project skill, and the
licence files are copies of the repository's own.
