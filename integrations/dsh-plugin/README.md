# RAGRAF for DeepSeek Harness — one plugin instead of a hand-edited profile

RAGRAF indexes a repository on your machine and answers *where something is defined*, *who
calls it* and *what breaks if I change it* — with a `path:line` for every fact. This package
is its DeepSeek Harness bundle: the MCP server, the session hooks and the index-first gate,
wired declaratively and installed by `dsh plugin add`.

## Why a bundle, and not a block in `cordis.patch.yml`

The previous installation lived in `~/.dsh/profiles/<profile>/cordis.patch.yml`: one `insert`
for the MCP server, one for the hook bridge. It worked, and then it stopped working silently.
Measured on this machine, 02–03.11.2026:

| What happened | What it looked like |
|---|---|
| Client update 44.7.0 dropped the hook-bridge packages from the build. The profile entry stayed and pointed at nothing | Hooks did not run for a week. No error anywhere: sessions started, tools worked, gate decisions simply never appeared |
| Three `insert` entries of the same bridge plugin (xerj, bsg, RAGRAF) | Only one mounted. The gate received **zero** decisions for a week |
| Hook command began with a quote (`"C:\Program Files\…\pwsh.exe"`) | PowerShell `ParserError`, exit 1 — reproduced, then fixed by using short paths without quotes |
| Instruction budget 65 536 against a 74 442-byte `~/.dsh/AGENTS.md` | DSH dropped the file **whole**: the model saw `Workspace instruction budget 65536 bytes: omitted ~/.dsh/AGENTS.md` |
| MCP server started with `cwd: C:\AutoClaw\RAGRAF` | Tools answered about the wrong corpus until the model called `use_project` |

A package fixes the class of problem, not the instance: it declares the bridge as its own
dependency, carries a version, is visible in the Plugins page, and can be removed without
archaeology.

## What the bundle declares

`cordis.patch.yml` inserts three rows and overrides one:

| Row | Why |
|---|---|
| `mcp-ragraf` — `@deepseek-ai/dsh-mcp-client`, stdio | the server itself; tools appear as `mcp__ragraf__*` |
| `hooks-ragraf` — `@deepseek-ai/dsh-hooks-claude-code` | session hooks, pointing at `$DSH_HOME/ragraf/hooks.json` |
| `agent-instructions` — `maxBytes: 196608` | so a grown global `AGENTS.md` is not dropped whole |
| — | no `--project` and no path in the configuration: `dispatch.py` starts the server with the RAGRAF home as its working directory, so it always begins on a known corpus, and a foreign directory is named through `use_project` |

Every path is computed at load time (`$DSH_HOME`, else `~/.dsh`); the package contains no
machine-specific path, so the same file works on another computer. The interpreter defaults
to `python` from `PATH` and is resolved further by `scripts/dispatch.py` (the checkout's
`.venv`, then the installed package). Override it with `RAGRAF_PYTHON`.

## Install

The plugin ships glue, not the indexer. RAGRAF itself must be present — a checkout
(`RAGRAF_HOME`, or a known location such as `C:\AutoClaw\RAGRAF`, `~/RAGRAF`, `/opt/ragraf`)
or the Python package `ragraf`.

```powershell
# from a RAGRAF checkout — writes the machine files, installs the bundle, verifies it
python tools\install_dsh_plugin.py install --profile desktop

# or by hand
dsh plugin --profile desktop add <path>\integrations\dsh-plugin
dsh --profile desktop --dump-config      # the composed tree, with !!js evaluated
```

Then **restart DeepSeek Harness**: the hook configuration is read once at start-up. Until the
restart the previous composition stays in memory and the new one is not used.

Check without changing anything:

```powershell
python tools\install_dsh_plugin.py check --profile desktop
python $env:USERPROFILE\.dsh\ragraf\dispatch.py doctor --profile desktop
```

Remove:

```powershell
dsh plugin --profile desktop remove ragraf-dsh
```

## What `install_dsh_plugin.py` does, step by step

1. Resolves the installation and the interpreter (`.venv` of the checkout first).
2. Builds `$DSH_HOME/ragraf/hooks.json` from the project hook configs — xerj, bsg and RAGRAF —
   with the RAGRAF stages rewritten to go through `dispatch.py`, gate mode `deny`, commands
   without leading quotes. Other projects' hooks are copied verbatim: RAGRAF does not own
   their behaviour.
3. Copies `dispatch.py` and writes `install.json` (paths, versions, timestamp).
4. Copies this package to `$DSH_HOME/ragraf/bundle/` and installs it with
   `dsh plugin --profile <p> add`, reading the pnpm store directory from the profile (a
   different store makes pnpm refuse to link).
5. Removes the old `mcp-ragraf` and `hooks-shared` entries from the profile layer — with a
   timestamped copy next to it — because two entries of the same bridge plugin do not both
   mount.
6. Verifies with the client's own oracle, `dsh --profile <p> --dump-config`: both rows are in
   the composed tree, the `configPath` resolves to a file that exists, the gate mode is the
   requested one. On failure the profile layer is restored from the copy.

`--dry-run` prints the steps and touches nothing.

## Boundaries, named

* **The hook needs a checkout.** The gate stages live in `tools/harness_gate.py`, which is not
  part of the Python package. With a package-only installation the server and tools work, and
  every hook exits 0 with a reason on stderr. A silent "allow" would be worse: the gate would
  look alive.
* **The hook never reindexes.** A full `ensure` of a mid-sized corpus takes minutes; the
  SessionStart hook is given 30 seconds. Freshness is reported, the model is asked to call
  `ensure` first, and the gate refuses file search until it happened.
* **The instruction budget cannot be raised for preset sessions.** With agent presets
  (`agent-preset-registry` → `standard`), the effective `maxBytes` belongs to the preset's own
  `agent-instructions` row; the top-level row is disabled by the base layer. Overriding a
  preset replaces its whole child list and freezes it against client updates, so this bundle
  does not do it. `install_dsh_plugin.py check` measures the effective budget and says so
  plainly instead of reporting success.
* **No telemetry.** The index, the vault and the usage journal are local files.

## Marketplace

`index.json` is a plugin-market index in format 1, validated by the client's own
`parseMarketIndex` (`@deepseek-ai/dsh-plugin-market-index`) — including a negative control in
`tests/test_dsh_plugin.py`. It pins a git commit, so a version always resolves to the same code:

```powershell
python tools\build_dsh_market_index.py     # rebuild after a release
```

Serve the file over HTTPS (for example from GitHub Pages) and add its URL in **Plugins →
Discover → Add index source**.

## Licence

Business Source License 1.1 — see the repository `LICENSE` and `NOTICE`. Paid tiers are
described in `EULA.md`.
