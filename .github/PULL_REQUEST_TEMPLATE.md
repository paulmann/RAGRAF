# Pull request

> The project's one rule: **a useful change is a fact with a reference.**
> `file:line`, a reproducible command with its output, or a measurement artifact.
> See [CONTRIBUTING.md](../CONTRIBUTING.md).

## What this changes

<!-- One paragraph. Name the measured problem, not just the solution. -->

## Evidence

<!-- The defect this closes, the measurement that motivated it, or the doc that
     diverged. Include `file:line` references and the exact command output. -->

- Motivation / issue:
- Index revision used (`ragraf index_status` or `ragraf status`):
- Evidence (`file:line`):

## How it was verified

<!-- Paste the commands you ran, with their verdict lines. -->

```text
command →
result  →
```

- [ ] Targeted suites pass (standalone scripts, e.g. `python tests/test_watch.py`)
- [ ] Project doctrine checker passes: `pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1`
- [ ] LF endings, UTF-8 **without BOM** in every touched file
- [ ] Numbers in `README.md` / `CONTRIBUTING.md` still match the code if counts changed
- [ ] Documentation updated in the same change, or explicitly declared out of scope

## Degradation and failure modes considered

<!-- Which of these does the change touch, and what happens when the resource is
     missing: interrupted indexing, repeated delivery, concurrent CLI + MCP,
     no Joern, no embedding model, a foreign `python`. "Not applicable" is a
     valid answer — say why. -->

## Checklist

- [ ] Diff is minimal — no drive-by reformatting or renaming
- [ ] No secret material, no `.ragraf/` index data, no private corpus paths
- [ ] If an invariant or a documented count changed, `CHANGELOG.md` and the checker were updated in the same commit
