# Security Policy

## Threat model (honest version)

RAGRAF is a **local, single-user developer tool**. Its security boundary is the user's own machine and profile. What it protects and what it does not:

| Asset | Protection | Status |
|---|---|---|
| Secret-bearing corpus files | `secrets_exclude` — files are never opened, indexed, embedded or logged (verified by pattern tests) | ✅ by design |
| Config credential references | only the *fact* of a reference is indexed, never values (`db.dsn_from`) | ✅ by design |
| Query content in telemetry | arguments stored as sha1 + 60-char digest, not full text; journal is a local SQLite file | ✅ with a caveat (digest is plaintext-truncated) |
| Index/graph data | local files under `<project>/.ragraf/`; no network exposure | ✅ |
| Admin panel mutations | loopback-only bind, POST-only, session token (`secrets.compare_digest`), project-config writes validated + `.bak` backup | ⚠️ see known issues |
| Against a malicious web page (DNS rebinding) | `Host` and `Origin` are validated in `parse_request` before routing: a request naming another host, another port or another origin gets 403 with the reason in the body. Absent `Origin` is allowed (curl, scripts); `Origin: null` is refused | ✅ closed 24.10.2026 — `tests/test_admin_origin.py`, 38 properties |

## Known open issues (tracked transparently)

This project's principle is that an honestly published known gap beats a silently patched one. Current items (full detail in `docs/DEFECTS.md`):

- **C-01 — CLOSED 24.10.2026** (was: the admin panel token endpoint handed the token to any local client and `Host`/`Origin` were not validated). Two separate fixes, both measured: (1) `/api/token` returns only access *state* (password set? token count? scopes) and never a secret — the old behaviour was a working leak, not a wording issue; (2) `Handler.parse_request` validates `Host` (loopback name **and the panel port**) and `Origin` (only `http://<loopback>:<panel port>`) before any route is dispatched, so the static subclass that overrides `do_GET` is covered too. Verified by behaviour, not by reading the source: `tests/test_admin_origin.py` starts the real handler on a free port and sends requests with foreign headers — before the fix a request with `Host: evil.example:<port>` received **200**; now **403**. Checker rule `Test-AdminPanelOrigin` runs it and turns red on corruption. **What remains:** a local process running with the same user rights can still read the panel's state files — that is outside this threat model (the panel is a single-user local tool), and it is stated here instead of being implied to be covered.
- **C-04 — CLOSED 24.10.2026** (was: Joern `.bat` launch used `shell=True` with hand-rolled quoting). Measured first, on a live `.bat` wrapper: an argument containing `&` without spaces **split the command** (`a&b` became `probe.bat a` plus an attempt to run `b`, exit 1), `a^b` arrived as `ab`, and `%TEMP%\x` was expanded into a real path. Now the launch is `cmd.exe /d /s /v:off /c` with every argument quoted by `util.cmd_command`; `&`, `^`, `|`, `>`, `!`, parentheses and spaces arrive verbatim (verified: `tests/test_cmd_quoting.py`, 18 properties). **What remains, stated plainly:** `%` and `"` cannot be passed through `cmd.exe` at all — cmd expands `%NAME%` inside quotes and no quoting form prevents it — so such a path is **refused with a named reason** (`util.CmdArgumentError`) and the index degrades to no code-property graph instead of running a silently different command. Keep RAGRAF and Joern under paths without `%`.

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security problems.**

1. Email: **mid1977@gmail.com** (maintainer; PGP key on request).
2. Include: reproduction steps, affected version/commit, impact assessment, and — in the project's spirit — a *fact with a reference* (`file:line` or exact command+output).
3. Expected response: acknowledgment within 7 days; coordinated disclosure timeline agreed before any publication. Credit in `CHANGELOG.md` («Found by») unless you request otherwise.

## Supported versions

| Version | Supported |
|---|---|
| 0.2.x (main) | ✅ — security fixes land on `main` and are tagged |
| < 0.1 | ❌ — pre-release snapshots |

## Scope notes

- The optional Joern/JDK layer is Apache-2.0 third-party software installed separately; its vulnerabilities are upstream's scope (we pin versions and verify SHA-256 on download — `scripts/install-joern.ps1`).
- The embedding model download comes from HuggingFace via `fastembed`; under TLS inspection use `truststore` rather than disabling verification. There is no certificate-verification bypass anywhere in the codebase, and adding one will be rejected.
