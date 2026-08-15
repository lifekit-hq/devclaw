# Contract — Slice-guard reads tasks.md (US1, FR-005)

**Where**: `devclaw/goal/slice_guard.py` (detection) + `devclaw/goal/tick_settle.py`
(call site). Layer 2, settle-time (post-session, work-present — never idle).

## Function shape (unchanged signature style; source file changes)
- `tasks_flips_sync(workspace_dir) -> int` — the build-ahead signal, replacing
  `mega_dump_flips_sync` (which read `PLAN.md`).
- Reads every `specs/*/tasks.md` at `HEAD^` and `HEAD` via
  `git show <ref>:<path>`; counts unchecked→checked flips
  (`- [ ]` → `- [x]`) summed across all files.

## Behavior
| Situation | Result |
|---|---|
| ≥1 `specs/*/tasks.md` present | sum `[ ]→[x]` flips across all (HEAD^→HEAD) |
| no `tasks.md` anywhere, `PLAN.md` present | **legacy fallback**: PLAN.md flips (removed by US4/shrink) |
| neither present | `0` (fail-OPEN on **detection**, as today — not a wedge) |
| git hiccup / no parent commit / not a repo | `0` (best-effort, never raises) |

## Consumption (fail-closed lives here — Principle V)
`tick_settle.py` compares the flip count to the per-increment threshold. Under
`strict`, `> threshold` ⇒ build-ahead ⇒ gate the increment **closed** with an
actionable reason ("this increment checked off N tasks.md items built ahead into
later stories"). Under `trust`, per ADR 0007 / spec 001, it advises-and-ships with
the finding surfaced. **The source file changed (`PLAN.md`→`tasks.md`); the
consequence did not.**

## Invariants
- Zero-token (git subprocess only), settle-time only (Principle III).
- Never raises (best-effort detection); the *gate* enforces closed-ness.

## Test (named regression, SC-003)
`test_slice_guard_tasks.py` — builds a realistic repo (real `git init`, a
`specs/NNN/tasks.md` with `[ID] [P?] [Story]` items), commits a slice that flips
>1 item, asserts the guard reports the flips **from tasks.md** and that **`PLAN.md`
is never read** (assert absence). Includes: absent-tasks.md → legacy fallback;
neither → 0.
