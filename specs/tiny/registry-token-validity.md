# TinySpec: Registry token fails LOUD when present-but-invalid

**Branch**: `fix/registry-token-validity`
**Date**: 2026-08-31
**Status**: implemented
**Complexity**: small

## What

`NODE_AUTH_TOKEN` is forwarded into every sandbox unvalidated. A set-but-invalid
value degrades silently: `npm ci` 401s inside the sandbox, the worker
self-reports BLOCKED, and the goal burns its dispatch budget on an environment
fault it cannot fix. Validate the token's SHAPE at deploy time and its LIVE
AUTH from doctor, so a bad credential is caught before it eats a night.

## Context

Observed 2026-08-31: the Actions secret (set 2026-08-28) held a value with a
non-GitHub prefix. Plumbing was correct end to end — secret → deploy step env →
compose → `devclaw-mcp` container → `-e NODE_AUTH_TOKEN` on the sandbox — so
every hop reported success while `api.github.com/user` returned 401. Two
dispatches of `fs-479-outflow-honesty-2026-08-27` were consumed; the goal
parked at `dispatch cap 2 reached` with no delivered increment.

`specs/tiny/sandbox-registry-read-token.md` (#724) specified only the *unset*
case ("blank ⇒ no forward, byte-identical"). Set-but-wrong was never
considered — this spec closes that gap. This is the fail-loud-over-silent-
degradation invariant applied to a credential, and the deployed-instance
sibling of it (spec 016 FR-014: state/boilerplate drift ships a doctor check).

| File | Role |
|------|------|
| `deploy/deploy-devclaw.sh` | Modified — shape assertion beside the `CLAUDE_CODE_OAUTH_TOKEN` preflight (l.44–51) |
| `devclaw/doctor/checks_instance.py` | Modified — `check_registry_token` + registration in `INSTANCE_CHECKS`; re-imports `REGISTRY_TOKEN_VAR` from `engine.sandcastle` (one home), mirroring `_OAUTH_TOKEN_VAR` at l.30 |
| `tests/test_doctor.py` | Modified — seeded-fault cases for the new check |
| `docs/reference/env-vars.md` | Modified — the `NODE_AUTH_TOKEN` row still says "unset/blank ⇒ byte-identical" with no word on validity |

## Requirements

1. A **non-blank** `NODE_AUTH_TOKEN` not matching `^(ghp_|github_pat_|ghs_|gho_)`
   fails the deploy LOUD (`die`, non-zero exit). A wrong credential is worse
   than none: it reaches the sandbox and 401s there instead of at the gate.
2. **Unset/blank stays a warning, not a failure** — the pre-token deployment
   posture is preserved exactly (no `-e` forward, frontend builds simply can't
   run). Only *set-but-malformed* is fatal.
3. Neither the deploy script nor doctor ever echoes the token value, in any
   verdict, log line, or error — presence, shape, and probe status only.
4. `check_registry_token` reports: `OK` (shape valid + probe 200, **or**
   unset), `FAIL` (shape invalid, or probe 401/403 — the credential is dead),
   `UNKNOWN` (probe unreachable). A probe that cannot run is **never** `OK` —
   an unverifiable credential must not read as a healthy one.
   *Revised during implementation:* unset was specified as `WARN`, which made
   a clean instance report unhealthy and broke
   `test_doctor_reports_healthy_affirmatively`. Unset is a supported posture
   (the pre-token deployment), so it is `OK` with the consequence named —
   the same convention `check_auth_setup_token` already uses. Only
   set-but-broken is a fault, which is the whole point of this spec.
5. The live probe is bounded (≤5s timeout) and lives in a module-global
   function so tests patch it in the caller's module, per the repo's
   collector convention. It never raises into the check.
6. `FAIL` carries an actionable remedy naming the fix: regenerate a
   `read:packages`-only classic PAT, `gh secret set NODE_AUTH_TOKEN`, redeploy.

## Plan

1. Add the shape assertion to `deploy/deploy-devclaw.sh`, matching the existing
   preflight's voice (`say` on success, `die` on malformed, `⚠` on absent).
2. Add `check_registry_token` to `checks_instance.py`: re-import
   `REGISTRY_TOKEN_VAR`, shape-test, then probe via the patchable global.
   Register it in `INSTANCE_CHECKS` beside the auth checks.
3. Seeded-fault tests in `tests/test_doctor.py` — this is the doctor
   seeded-fault tripwire class, so it earns a test: malformed shape ⇒ FAIL,
   probe 401 ⇒ FAIL, probe unreachable ⇒ UNKNOWN (never OK), unset ⇒ WARN,
   and the value never appears in any rendered finding.
4. Amend the `NODE_AUTH_TOKEN` row in `docs/reference/env-vars.md` to state the
   validity contract, and bump its `docs/INDEX.md` currency tag.

## Tasks

- [x] Shape assertion in `deploy/deploy-devclaw.sh`
- [x] `check_registry_token` + `INSTANCE_CHECKS` registration
- [x] Bounded, patchable live-probe global
- [x] Seeded-fault tests (malformed / 401 / unreachable / unset / no-leak)
- [x] `env-vars.md` row + `INDEX.md` currency tag

## Done When

- [x] All tasks checked off
- [x] A deploy carrying a malformed `NODE_AUTH_TOKEN` exits non-zero and names the fix
- [x] `doctor` on the live instance reports the registry token's real state
- [x] Full suite green; `ruff check .` and `mypy` clean
- [x] No token value appears in any deploy output, finding, or test fixture
