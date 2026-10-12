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
| Against a malicious local process / malicious web page (DNS rebinding) | **not protected today**: `GET /api/token` answers any local caller and `Host`/`Origin` are not validated | 🔴 known open — tracked publicly |

## Known open issues (tracked transparently)

This project's principle is that an honestly published known gap beats a silently patched one. Current items (full detail in `docs/DEFECTS.md`):

- **C-01** — admin panel token endpoint hands the token to any local client; no Host/Origin validation (DNS-rebinding exposure while the panel runs). *Mitigation until fixed:* run the panel only during active use (`--no-browser`), never port-forward it, close it when done. Fix tracked as roadmap **R-SEC-1**.
- **C-04** — Joern `.bat` launch uses `shell=True` with hand-rolled quoting; hostile characters in paths (`&`, `^`, `%`, `"`) can break or inject arguments. *Mitigation:* keep RAGRAF and Joern under paths without shell metacharacters. Fix tracked as **R-SEC-4**.

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security problems.**

1. Email: **mid1977@gmail.com** (maintainer; PGP key on request).
2. Include: reproduction steps, affected version/commit, impact assessment, and — in the project's spirit — a *fact with a reference* (`file:line` or exact command+output).
3. Expected response: acknowledgment within 7 days; coordinated disclosure timeline agreed before any publication. Credit in `CHANGELOG.md` («Found by») unless you request otherwise.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x (main) | ✅ — security fixes land on `main` and are tagged |
| < 0.1 | ❌ — pre-release snapshots |

## Scope notes

- The optional Joern/JDK layer is Apache-2.0 third-party software installed separately; its vulnerabilities are upstream's scope (we pin versions and verify SHA-256 on download — `scripts/install-joern.ps1`).
- The embedding model download comes from HuggingFace via `fastembed`; under TLS inspection use `truststore` rather than disabling verification. There is no certificate-verification bypass anywhere in the codebase, and adding one will be rejected.
