# Runnable-head lane selection (head-of-line blocking is a bug)

## What

The derived single-writer project hold names the first goal that can
actually ACT this sweep — not merely the first non-terminal one. A head
that cannot act (blocked, owing only its merge, or idle with no unread
steering and no due cadence) is skipped as candidate; the queued successor
takes the lane and the head reclaims it when runnable again.

## Context

Night of 2026-08-31: fs-479 sat idle waiting out its 1d re-plan cadence
(a lost close, #784) and held the finance-sentry lane; 7 runnable pointer
goals queued behind it produced zero work all night. Spec 025 FR-015 had
already skip-overed *blocked* heads (the 2026-08-28 OOM night); this
generalizes the same move to every cannot-act head.

**Ruled by Denys 2026-09-01: this is a BUG FIX, not a feature** — lane
stranding behind a non-runnable head is a defect in the selection rule.
Rejected alternative: spec'ing "skip-over" as a scheduler feature
(/speckit-specify) — rejected because the single-writer invariant, the
derived (never stored) hold, and in-flight-outranks-age all stand
untouched; only candidacy narrows, at the one existing seam
(`project_hold.holder_map`).

## Requirements

- Single-writer invariant untouched: at most one goal dispatches per
  project; a successor mid-task keeps the lane against a newly-runnable
  elder (in-flight outranks age); the elder reclaims at the next sweep
  with nothing in flight.
- "Runnable" is derived from the same cheap reads the tick's should_plan
  gate uses (in_flight / unread steering / cadence_due) — zero LLM, zero
  writes, hold stays derived-never-stored.
- Store-read failures in the runnable calc RAISE (module failure policy):
  a swallowed read would silently thin candidacy and switch single-writer
  off. Only a goal.yaml that won't load is skipped, as before.
- qa goals (scope None, empty cadence) never reach the cadence parse.

## Plan / Tasks

- [x] `project_hold.holder_map`: skip a candidate with no in-flight work
  when it owes only a merge (pending_merge_pr — the finalize runs before
  the hold gate by design) or has neither unread steering nor a due
  cadence. Module + derivation docstrings updated.
- [x] Tripwire tests (CAS/single-writer class), verified to bite:
  `test_idle_head_with_nothing_to_do_releases_lane_to_runnable_successor`
  (fails unfixed) and
  `test_runnable_again_head_reclaims_lane_from_idle_successor` (pins
  reclaim-by-age doesn't regress).
- [x] `docs/architecture.md` single-writer section + INDEX currency tag
  (the passage also still claimed blocked goals hold — stale since
  spec 025; corrected here).

## Done-When

- The two named tests pin both facets; the pre-existing hold tests
  (queued-zero-cognition, in-flight-outranks-age, blocked skip-over,
  terminal handover) stay green unchanged.
- Full suite, ruff, mypy green.
