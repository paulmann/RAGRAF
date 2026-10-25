# Contributing to RAGRAF

Thank you for considering a contribution. This project holds contributions to the same standard it holds its own facts: **verifiable**.

> **The one rule: a useful contribution is a fact with a reference.**
> `file:line`, a reproducible command with its output, a measurement artifact. Everything else is a conversation starter — and conversations are welcome too, just label them as such.

Full version with the development setup, conventions and test map: **[the wiki](https://github.com/paulmann/RAGRAF/wiki/13-Contributing)**.

## Quick links

| I want to… | Do this |
|---|---|
| Report a defect | Use the **Defect report** issue template — it enforces command → expected → actual → environment → `index_status` |
| Report a doc↔code divergence | Use the **Doc divergence** template (doc path + code `file:line`) — these are among the most valued reports |
| Ask a question, share a measurement on your corpus | Open a thread in [Discussions](https://github.com/paulmann/RAGRAF/discussions) — questions are welcome, they do not need a defect behind them |
| Report a security problem | **Not** a public issue → see [SECURITY.md](SECURITY.md) |
| Propose a feature/architecture change | Open a discussion issue **before** writing the patch; include the measured problem, not just the solution |
| Send a patch | Fork → branch → follow the conventions below → make CI green → reference the defect/measurement that motivated the change |

## What is welcome (in order of value)

1. **Reproducible defects** with full environment context.
2. **Documentation diverging from code** (doc path + `file:line` + which side is wrong).
3. **Silent degradations** — the contract is «no silent degradation»; anything that broke quietly is a P1-class finding even if the underlying bug is small.
4. **Measurements on your corpus** — run the M1/M3-style protocol (fixed tasks, baseline captured *before*, JSON artifacts). Unfavorable numbers are welcome; the benchmark doctrine forbids cherry-picking.
5. **Architecture discussions before large patches.** The project is developed for concrete tasks; big PRs without a prior conversation will likely be redesigned.

## What will likely be declined

- Drive-by rewrites without a measured defect behind them.
- Adding a graph query language — decision **P4** is evidence-backed (LLMxCPG: base models produce valid CPGQL in 19–132/1278 cases) and final absent new evidence.
- Services/daemons in the base install — SQLite-files-only is a principle.
- «Prettier» answers at the cost of `limits`/provenance honesty (FT-23).

## Conventions (enforced by `tools/Invoke-ProjectDoctrine.ps1`, 14 checks)

```powershell
pwsh -NoLogo -NoProfile -File tools/Invoke-ProjectDoctrine.ps1
```

| Rule | Detail |
|---|---|
| PowerShell 7.6+ only | `#Requires -Version 7.6` + `-PSEdition Core`; no `powershell.exe`, no WinPS-compat |
| Encodings | UTF-8 **without BOM**, LF in all shipped files — CI-checked |
| Comments carry evidence | The codebase's signature style: «verified by run / measured on \<date\>» with the observed fact. Keep it |
| Fixed dependencies | bounds in `pyproject.toml`; upgrades are deliberate acts (NFR-7) |
| No silent degradation | every fallback reports itself: `capability`, `reason`, `limits` |
| Deterministic identifiers | content hashes only — no UUIDv4, no timestamps inside IDs |
| Artifacts isolated | index data lives in `<project>/.ragraf/` only |

## Development setup

```bash
git clone https://github.com/paulmann/RAGRAF.git && cd RAGRAF
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -e .
python tests/test_lang_routing.py               # portable suites run standalone (no pytest yet)
```

New tests: **synthetic fixtures only** (`tests/fixtures/`), portable, standalone-script style (`check(cond, title)` + PASS/FAIL counters). A test that requires a private corpus does not ship.

## Roadmap

Planned work lives in [ROADMAP.md](ROADMAP.md) with stable IDs (`R-SEC-1`, `R-PERF-1`, …). If your idea is already there — reference the ID; if it isn't — propose it as an issue first.

## Recognition

Accepted defect reports are recorded with the reporter's handle in [CHANGELOG.md](CHANGELOG.md) under «Found by». The project keeps provenance for contributions the way it keeps provenance for graph edges.

## License

By contributing, you agree your contributions are licensed under the project's [Business Source License 1.1](LICENSE) — the same terms the project ships under — and that the maintainer may relicense them under the terms described in [CLA.md](CLA.md). Participation in issues, discussions and pull requests is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).
