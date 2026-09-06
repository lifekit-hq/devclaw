# TinySpec: undeclared-registry advisory sees a nested `.npmrc`

**Branch**: fix/issue-819-nested-npmrc
**Date**: 2026-09-06
**Status**: done (companion finance-sentry PR outstanding)
**Complexity**: small

## What

Widen doctor's spec-030 FR-005a advisory (`project.capabilities.undeclared`)
so it sees a private-registry dependency that does not live at the workspace
root. Today it reads only `<workspace>/.npmrc` and
`<workspace>/package-lock.json`; finance-sentry's registry config is
`frontend/.npmrc`, so the advisory reported "no undeclared registry dependency
visible" for a repo that depends on GitHub Packages and declares no
`registry:*` capability (devclaw #819).

## Context

The declaration is the project's own statement of what its verify contract
depends on. Since #816 the token is required instance-wide, so the credential
can no longer be silently absent — but the spec-030 probe holds dispatch on a
*rejected* (rotated/expired) token, and it only runs for projects that declare
the capability. An advisory that cannot see `frontend/.npmrc` is why, on
2026-09-03, nothing held at admission and a worker burned a session on an
`npm ci` 401.

| File | Role |
|------|------|
| `devclaw/doctor/checks_project.py` | Modified — `check_capability_declaration` scans root + one level of subdirectories, and the manifest `verifyCmd` text |
| `tests/test_doctor.py` | Modified — the existing advisory case (l.391) grows the nested and verifyCmd shapes; no sibling test |

## Requirements

1. A private-registry host in `.npmrc` / `package-lock.json` at the workspace
   root OR in an immediate subdirectory (depth 1 — the `frontend/` shape) is
   found, and WARNs with the same remedy as today.
2. The evidence names the path relative to the workspace (`frontend/.npmrc`),
   so the operator knows which file to look at.
3. A `verifyCmd` whose text names a private registry host is evidence too — a
   repo can point `npm ci` at GitHub Packages without a checked-in `.npmrc`.
4. Still never nags a public-registry project, still advisory-only (WARN, never
   FAIL, never a hold), still bounded: no recursion past depth 1, capped root
   count, capped head reads, no network, no cognition.
5. Declaring `registry:npm-github` settles it, unchanged.

## Plan

1. `_registry_scan_roots(ws)` — the workspace plus its immediate
   subdirectories, skipping dot-dirs, symlinks and dependency/output dirs
   (`node_modules`, `vendor`, `dist`, `build`), capped.
2. `_registry_evidence_sources(ws, manifest)` — a lazy `(label, text)`
   generator over the two evidence files under each root, then the manifest
   `verifyCmd`. Keeps the bounded-head-read budget per file.
3. `check_capability_declaration` iterates the generator; first private-host
   hit WARNs with the label in the evidence.
4. Extend the named seeded-fault test with the nested and verifyCmd shapes.

## Rejected alternatives

- **A bare `npm ci` in `verifyCmd` ⇒ WARN.** The issue's done-when reads "or in
  a `verifyCmd` that runs `npm ci`". Taken literally that WARNs every
  public-registry npm project with the remedy `add "capabilities":
  ["registry:npm-github"]` — advice that is simply wrong for a project that
  resolves against the public registry, and it repeals the check's own
  "must not nag every public-registry project" clause (its named test). The
  `verifyCmd` is therefore treated as one more *text source* scanned for a
  private-registry host (requirement 3), not as standalone evidence.
- **Unbounded recursive walk for `.npmrc`.** A doctor check runs over every
  registered project on every report; walking a monorepo's tree is not the
  couple of stats FR-005a promised. Depth 1 covers the shape that actually bit
  us and stays mechanical.
- **Parsing `cd <dir>` / `--prefix <dir>` out of `verifyCmd` to add scan
  roots.** Speculative: no observed repo puts its npm project deeper than one
  level. Add it when a repo does.

## Tasks

- [x] `_registry_scan_roots` + `_registry_evidence_sources` in `checks_project.py`
- [x] `check_capability_declaration` reads the generator; evidence names the
      workspace-relative path
- [x] Extend `test_undeclared_private_registry_dependency_is_advisory_only`
      with the nested (`frontend/.npmrc`) and `verifyCmd` shapes
- [ ] **Companion (finance-sentry repo, separate PR)**: `devclaw.json` declares
      `"capabilities": ["registry:npm-github"]`

## Done When

- [x] Nested `frontend/.npmrc` on an undeclaring project WARNs, naming
      `frontend/.npmrc`, with the unchanged remedy
- [x] The public-registry project stays OK; the advisory is still never a FAIL
- [x] Full suite + `ruff check .` + `mypy` green
