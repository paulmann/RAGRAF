# Recovery Recipes

Reference for the `ragraf-core` skill. Every recipe addresses a state that has
already occurred in this project or is described by the code. Order: diagnosis, then
action, then verification of the result.

> **Translation note:** recipes referencing author-internal tools
> (`measure_safe_excludes.py`, `check_ragraf_config.py`, `check_service_excludes.py`,
> `measure_dedupe_rules.py`, `tests/test_dedupe.py`, `tests/check_admin_*.py`) name
> them as they are cited by the shipped source comments; those tools are **not
> shipped** in the public repository. Where a recipe depends on them, an equivalent
> manual check is given.

## 1. The index is stale or «trembling»

**Symptom.** Operations answer, but the facts do not match the code; `freshness`
reports a divergence; `check` repeats reindexing on every call.

**Diagnosis.**

```powershell
python tools/ragraf.py -p config/ragraf.yaml check
python tools/ragraf.py -p config/ragraf.yaml status
```

`check` compares the corpus checksum with the one recorded in the revision. If the
checksums match but `stale` does not clear, the problem is not the files but the
revision record.

**Action.**

```powershell
python tools/ragraf.py -p config/ragraf.yaml reindex
```

**The cause of looping, if it repeats.** The checksum must be computed over exactly
the files that land in the index, and after duplicate collapsing
(`src/ragraf/corpus.py:59-70`, `:242-253`). If collapsing is moved into the indexer
or the guard, the checksum diverges from the index contents and reindexing never
stops. The check for this case is `tools/Invoke-ProjectDoctrine.ps1`, the collapsing
rule.

## 2. The registry shows an empty revision and zeros

**Symptom.** `python tools/ragraf.py catalog` shows `rev-` «none», `nodes 0`,
`edges 0` for a corpus, while `status` of the same corpus reports a revision and nodes.

**Cause (measured, historical).** Registering a corpus via `ensure` used to register
the development before indexing and did not write the run result into the registry
(`src/ragraf/ensure.py:308-312`). The entry stayed with trigger `register` and zeros.
**This is fixed in the shipped code**: on successful indexing `ensure` now calls
`record_index_run(..., trigger="ensure:index")` (`src/ragraf/ensure.py:338-348`).

**Action — immediate workaround (still valid if you ever see zeros).**

```powershell
python tools/ragraf.py -p config/ragraf.yaml index
```

This path writes the result into the registry (`src/ragraf/cli.py:247`). A second
path is any call that makes the guard work: the guard records the result itself
(`src/ragraf/watch.py:554`). Confirmation after the action — `catalog`: the entry
gains a revision, node and edge counts and a trigger of the form `guard:fast`.

**The proper fix (already applied).** Write the result in `ensure` after successful
indexing by calling `catalog.record_run` (`src/ragraf/catalog.py:235`) with the
numbers from the read revision — exactly as the guard does. After such a change:
`python tests/test_ensure.py`, then re-run the checker: the registry-coverage rule
must go green.

## 3. `capability=degraded`

**Symptom.** `index_status` reports `capability=degraded`; the slice operation refuses.

**What it means.** The code-property layer was not built or its export failed; the
other layers (lexicon, vector, parse graph) work. Silent degradation does not exist:
`src/ragraf/cpg.py:9-11`.

**Diagnosis.**

```powershell
python tools/ragraf.py -p config/ragraf.yaml status
```

The `cpg` stage will hold the reason. A known case on our own corpus: the export
returned code 1 with an error of the form «next on empty iterator», while the code
property graph file was in fact built. The cause was not localized — that is a
defect of the layer, not of the configuration.

**Action.**

1. Make sure Java and Joern are available and located where the code looks for them
   (`src/ragraf/cpg.py:82-93`, `:391`).
2. Restart indexing: the layer is rebuilt from scratch.
3. If the refusal repeats — work without the slice operations and record the finding
   separately; do not present `degraded` as the whole index being broken.

**Important.** The Joern server is stopped automatically when the indexer closes
(`src/ragraf/indexer.py:1166-1183`). An unclosed server is a multi-gigabyte hanging
process holding the port; check the process list after manual experiments.

## 4. An exclusion edit threw out working code

**Symptom.** Files that were visible in search disappeared; no errors reported.

**Cause.** Pattern matching is case-sensitive and understands only «any directory
tail» templates for cutting directories: `**/cache/**` catches `src/cache/` but not
`src/Cache/` (`src/ragraf/util.py:72-158`).

**Action.**

The author-internal measurement tools are not shipped; the equivalent manual check:

```powershell
# 1. Count what the corpus scans to BEFORE the change
python tools/ragraf.py -p config/ragraf.yaml status
# 2. Apply the exclusion change, then:
python tools/ragraf.py -p config/ragraf.yaml check
python tools/ragraf.py -p config/ragraf.yaml reindex
# 3. Compare the file count in status; investigate EVERY file that disappeared
python tools/ragraf.py -p config/ragraf.yaml query search "<name of a vanished module>"
```

The rule is simple: if extending the base exclusion list throws out zero files in
every corpus — it is safe; if even one — the name goes into the specific corpus's
config, where the directory's purpose is visible.

## 5. Duplicate collapsing and its price

**Symptom.** The file count in the index is lower than the file count on disk.

**What it is.** Copies with identical content collapse; one primary remains; files
on disk are not deleted. By default collapsing is OFF (`src/ragraf/config.py:541`),
and our own corpus config has no section for these settings — meaning it is not
applied to that corpus. When enabled, the index state carries the discarded
counters: how many copies, how many groups, bytes and lines
(`src/ragraf/indexer.py:779-786`).

**What must not be done.** Changing the primary-copy selection rule to «the first
one that comes along»: a tie must be resolved by path, otherwise the corpus
checksum trembles and the guard loops (`src/ragraf/dedupe.py:165-223`).

**Verification.** The author-internal `tools/measure_dedupe_rules.py` and
`tests/test_dedupe.py` are not shipped; the portable check is the guard behavior:
run `check` twice in a row — the second run must not trigger reindexing.

**Remember.** Enabling collapsing changes the corpus checksum: the guard reindexes
the corpus once. That is a conscious consequence, not a defect.

## 6. The computer fell back to CPU

**Symptom.** `index_status` reports the CPU computer; indexing time is higher than
expected.

**Causes (in check order).**

1. The GPU is disabled in the settings.
2. The volume of work is below the break-even threshold: preparing the map takes
   about a quarter of a minute one-time, so on a small number of texts it loses
   (`src/ragraf/config.py:276-301`).
3. The package the GPU needs is not installed: `sentence-transformers` and `torch`
   are not declared in the package description (`src/ragraf/compute.py:141-146`).
4. Free GPU memory is below the threshold — fallback to CPU with a reason.

**Action.** Look at the `reason` field in the compute state: the reason is recorded
there, not guessed. Do not lower the threshold «so the GPU works» — that slows the
work down.

## 7. The admin panel

```powershell
python tools/ragraf-admin.py --no-browser
```

Local access only, port 8777; the token is printed at startup and handed to the page
by a separate request. Read requests need no token; any settings change requires it
and is written with a backup copy plus key and bounds validation
(`src/ragraf/settings.py:327-386` — project-config path; note that the *global*
settings store writes without a backup — roadmap R-SEC-3). Live checks of the panel:
`python tests/check_admin_agents.py` and `python tests/check_admin_dedupe.py`
(*not shipped*; the panel must be running).

> **Security note (added in the 2026-10 revision):** the panel's `GET /api/token`
> endpoint returns the access token to any local client without authentication, and
> the page fetches it automatically. Until roadmap R-SEC-1 is implemented, treat the
> token as protection against *accidental* changes only — not against a malicious
> local process or DNS-rebinding. Do not expose the port; keep the panel bound to
> 127.0.0.1 (the default).

## 8. What to do in any of these states

1. Write down the measurement: the command, the output, the revision, the checksum.
2. Distinguish a pre-existing state from the consequence of your own edit: the
   checker's baseline is recorded in `AGENTS.md` §10 (*not shipped*) — **2 errors,
   0 warnings**.
3. Do not report a green result without inspecting the object: every check in the
   checker has a counter of inspected objects, and zero on success counts as a
   false PASS.
