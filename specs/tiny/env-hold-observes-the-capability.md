# Tiny spec — a worker-reported env gap heals on the gap, not on devclaw's build

## What

**Amends spec 032 US2 / SC-004.** A worker-reported environment deficiency no
longer auto-heals when `instance_env_ref()` changes. It heals when a human
vouches (`resume_goal`), and `resume_goal` is wired to actually clear the row.
Declared-capability holds, which have real probes, are UNCHANGED.

## Context

`fs-431-hygiene-sentinels` blocked at 2026-09-07T00:58Z on a real gap — no
GitHub token with `actions:read`, so a failing CI job's log is unreadable. At
05:18Z it logged:

> auto-resumed: required capabilities are green again (heal 1/5)

Nothing about the token had changed. `read_result` (`env_cap.py:194`) rewrites
a worker-reported red row to **green** whenever `env_ref` differs, and
`instance_env_ref()` is `sandbox image | devclaw git sha`. #827 merged at
02:07Z, the box redeployed, the ref moved, the hold evaporated, and the goal
was re-dispatched into the same sandbox.

SC-004's premise — *"a new sandbox image or devclaw build IS the fix
arriving"* — fails twice here, and the second failure is the fatal one:

1. **Too coarse for a self-hosting instance.** The sandbox image is *tagged
   with the devclaw commit sha* (`devclaw-sandbox:cdc1b88…`), so both halves of
   the ref move on every devclaw merge. devclaw merges its own PRs several
   times a day, so a hold's real lifetime is "until the next unrelated merge" —
   hours — not "until someone provides the token."
2. **Anti-correlated with the fixes that matter.** The gaps workers actually
   report are credentials and env vars (`actions:read`, `NODE_AUTH_TOKEN`).
   Those ride environment variables, not the image — so the real fix does NOT
   move the ref, while everything irrelevant does. No refinement of the
   fingerprint can repair this; the signal is pointing at the wrong fact.

The row's own `remedy` text already names the correct exit: *"the hold clears
when the instance's environment changes, **or resume_goal after fixing it by
hand**."* The second clause is the honest one. This spec deletes the first and
makes the second actually work — today `resume_goal` clears the goal's fields
but not the project's worker-cap row, so the goal would unblock, dispatch, hit
the still-red row and re-block.

**Rejected alternative — narrow the ref** (pin to the sandbox image digest, or
to a hash of the project's environment declaration). Fixes failure 1 only.
A credential appearing changes no digest, so the hold would then never clear
mechanically and we would have traded a false heal for a permanent one, with
more machinery.

**Deliberately out of scope: raising a typed Problem** for this park. Doctrine
says a human-gated block carries one, and this park predates that rule without
one. Adding it means choosing a timebox default, and both candidate defaults
are wrong here — "assume fixed, retry" re-creates the session burn this spec
removes, and "keep holding" is what already happens. The existing one-ping-per-
episode notify (`env_hold_notified`) already names the capability and its
remedy. Filed as follow-up, not smuggled in.

## Requirements

- **R1** `read_result` returns a worker-reported red row as RED regardless of
  `env_ref`. The rewrite-to-green branch is deleted.
- **R2** `resume_goal` clears the worker-reported capability rows for the
  goal's project — the human vouch IS the signal, and it must reach the
  project-scoped row, not only the goal's fields.
- **R3** Declared-capability probes are untouched: a real probe going green
  still auto-heals `mechanical:env` on the existing `env_heal_attempts` budget.
- **R4** `record_worker_deficiency` no longer re-pins on a changed `env_ref`
  (the pin has no consumer once R1 lands); a repeat report refreshes
  `probed_at_ms` only.

## Plan

1. `env_cap.py` — delete the `env_ref` green-rewrite in `read_result`; simplify
   `record_worker_deficiency`; keep `env_ref` on the row as provenance
   (which environment the report was made against) with a comment saying it is
   evidence, not a heal trigger.
2. `goal/service.py` — `resume_goal` clears the project's worker rows.
3. Tests — rewrite SC-004's test to pin the AMENDED behavior (a changed ref
   does NOT heal) and add the resume-clears-it case. Extend the existing class
   test, never a sibling.
4. Docs — `CLAUDE.md` + `architecture.md` state the amended rule.

## Tasks

- [x] T1 env_cap read_result + record_worker_deficiency (R1, R4)
- [x] T2 resume_goal clears the project's worker rows (R2)
- [x] T3 tests: amend SC-004's case, add the resume case (R1, R2, R3)
- [x] T4 docs honesty + INDEX currency

## Done when

- A devclaw redeploy does not clear a worker-reported env hold.
- `resume_goal` does clear it, and the goal dispatches instead of re-blocking.
- A declared-capability probe going green still auto-heals.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
