# Contract — Onboard: adopt or install speckit (US2, FR-002/003)

**Where**: `devclaw/server/tools.py` `onboard` (layer 1 tool; already writes
read-only briefs). Delivery reuse: `devclaw/delivery/`.

## Input
A repo/workspace devclaw is asked to work.

## Decision
- **`.specify/` present (committed)** ⇒ **ADOPT**: record the repo as speckit;
  write **no** `PLAN.md`; do **not** open a scaffolding PR.
- **`.specify/` absent** ⇒ **INSTALL**: generate the `.specify/` scaffold
  (templates, scripts, `workflow-registry.json`, constitution seed) and open a
  **reviewable PR** via the existing delivery path. **Never** a direct/silent
  commit to the default branch (FR-003, Principle VI).

## Post-conditions
| State | Feature work allowed? |
|---|---|
| adopted | yes |
| install PR **merged** | yes |
| install PR **open** | **no** — blocked; no half-installed execution (spec Edge Case) |

## Invariants
- No `PLAN.md` is ever written by onboard, in either branch (SC-001).
- Install is a PR, count of silent scaffolding commits = **0** (SC-004).
- Detection uses the committed `.specify/` dir, not gitignored `feature.json`.
- Not on the idle tick path (onboard/dispatch-time) — Principle III holds.

## Tests (named regression, SC-001/SC-004)
`test_onboard_speckit.py`:
- repo with `.specify/` → adopt; assert no `PLAN.md` written, no PR opened.
- bare repo → assert a **reviewable PR** is opened with the `.specify/` scaffold
  and **zero** direct commits to the default branch.
- bare repo with install PR still open → feature dispatch is blocked with an
  actionable reason.
