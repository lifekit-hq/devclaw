# Tiny spec — the slice guard observes the goal, not the clock

## What

Retire the mtime-based **dispatch-boundary** slice guard (`_active > 1` in
`tick_dispatch`) and the `mechanical:slice_hold` block it raises. Keep the
settle-path build-ahead detector and the "specs exist but none graded" check,
both of which are sound.

## Context

`fs-421-asset-dossier` and `fs-429-counterparty-flows` were hard-parked for four
days on:

> dispatch held 5 consecutive ticks — features with pending tasks block new
> work: specs/046-trendforce-source-recovery

`046` is a **sibling goal's live feature** (`fs-318` is working it), not this
goal's build-ahead. Two individually-correct decisions collided:

* **#679/#728** taught the dispatch boundary to tell "this goal's current
  feature" from "a prior run's leftovers" by comparing `specs/*/tasks.md`
  **filesystem mtimes**.
* **"one goal, one checkout"** then gave every goal a fresh `git clone --local`.
  In a clone, a file's mtime is *when git wrote it*, not when anyone authored
  it — and `prepare_workspace` restamps files on every branch placement.

So the discriminator became noise, and a sibling's active spec reads as this
goal's build-ahead. The mtime heuristic was itself a patch (#728) on a guard
whose premise was already wrong: *"the repo has 2+ features with pending tasks"*
does not imply *"this goal's next increment will build ahead."* A repo
accumulates spec dirs; that says nothing about the next dispatch.

Constitution IX settles it. Software owns safety, money, state, the verdict of
record, and the protocol. *"Is this increment sliced too wide?"* is a judgment
about the repo's work — it belongs to the agent, to the settle-path detector
(`tasks_flips_sync`, which compares `HEAD` against its first parent — the
goal's OWN commit, a real fact), and to the done-gate. Not to a filesystem
heuristic at the dispatch boundary. Deleting the guard removes a brake bound to
a proxy; it does not remove the protection, which already lives where it can be
computed correctly.

The block was also a dead end: `mechanical:slice_hold` has no heal path in
`tick.py`, and `resume_goal` re-enters the same 5-tick countdown. That is the
generalisable defect, so this spec also pins it (see Requirements R5).

**Rejected alternative — derive `current_dir` from the goal's branch diff**
(`merge-base(main, goal_branch)..goal_branch`). It fixes the signal but keeps a
dispatch-time brake whose premise stays a guess about work not yet done, and it
adds a git subprocess to a path that is currently pure-fs. The settle-path
detector already answers the same question from evidence instead of prediction.

## Requirements

- **R1** The dispatch boundary no longer holds or blocks on the count of
  features with pending tasks. `speckit_offending_dirs_sync` is deleted.
- **R2** The "spec dirs exist but none carry a `tasks.md`" hold is UNCHANGED —
  it reads a fact (the plan step never ran), not a prediction.
- **R3** `slice_hold_count` is retired: column dropped, model field removed,
  doctor check flipped to the legacy "dropped" shape (the
  `inbox_ingest_cursor` precedent).
- **R4** Goals currently parked on `blocked_kind="mechanical:slice_hold"` are
  released to `idle` by the boot migration — a retired brake must not strand
  the goals it parked.
- **R5** A structural tripwire asserts every `mechanical:*` blocked_kind the
  code can WRITE either has a heal branch in `tick.py` or is listed as
  deliberately human-gated. `mechanical:` means "cheaply re-checkable without
  an LLM"; a mechanical kind with no re-check is a contradiction, and this is
  what let `slice_hold` become a dead end unnoticed.

## Plan

1. `tick_dispatch.py` — drop the `_active > 1` branch and the `_SLICE_HOLD_CAP`
   escalation; keep the `_graded == 0` hold and the reset line goes with the field.
2. `slice_guard.py` — delete `speckit_offending_dirs_sync`; retarget
   `speckit_feature_state_sync`'s docstring at its surviving consumer.
   `current_feature_dir_sync` STAYS (done-gate grounding, executing-feature
   record, console) — only its dispatch-guard use goes.
3. `models.py` / `state.py` / `state_status.py` / `service.py` / `tick.py` —
   remove the field and its resets; add the release + `DROP COLUMN` migration.
4. `doctor/checks_instance.py` — `instance.dispatch.goal_status_slice_hold_count`
   becomes `instance.legacy.slice_hold_count_column`.
5. Tests — remove the five that pin the retired behavior (symmetric ratchet);
   add the R5 structural guard.
6. Docs — `docs/` currency + CLAUDE.md if it names the brake.

## Tasks

- [x] T1 dispatch guard + slice_guard surface (R1, R2)
- [x] T2 field retirement + migration + release (R3, R4)
- [x] T3 doctor legacy check (R3)
- [x] T4 remove retired tests; add mechanical-kind heal-path tripwire (R5)
- [x] T5 docs honesty pass

## Done when

- No dispatch is held or blocked on sibling features with pending tasks.
- `mechanical:slice_hold` appears nowhere in the code; parked goals are released.
- The R5 tripwire fails if a future `mechanical:*` kind ships with no heal path.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
