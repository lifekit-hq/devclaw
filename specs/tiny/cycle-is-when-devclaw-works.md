# Tiny spec — the run cycle is when devclaw works

## What

The cycle window that gates the cycle report AND the self-issue filer follows
the operator's run schedule: the configured window when one is enabled, the
whole calendar day when it is disabled (24/7). Retires the parallel
`DEVCLAW_RUN_CYCLE_START/END/TZ` knob.

## Context

The owner asked, correctly: *"we already have some mechanism that analyzes the
night and creates a problems list, and from that files GitHub issues — no?"*

We do. `goal/self_issue.py` runs at every cycle close (`service.py:543`),
zero-LLM, env-gated on `DEVCLAW_SELF_REPO` — which IS set on the box
(`lifekit-hq/devclaw`). It files one issue per problem fingerprint once the
problem has survived `>= SELF_ISSUE_MIN_CYCLES` (2) distinct run-cycles with at
least one terminal failure, and ages the issue out after 3 quiet days.

It has filed **1 issue against 40 catalogued problems**. Everything else sits
at `lifecycle: "identified"` forever.

The recurrence denominator is the bug:

```
problems_active_in_window(start, end)      # state_store/problems.py:320
  → WHERE last_seen_ms BETWEEN ? AND ?
start/end = most_recent_closed_window()    # hardcoded 22:00–05:00 Europe/London
```

That window is **7 of 24 hours**. The owner ruled 24/7 operation on 2026-09-05
and disabled the run window, but the filer still slices the day as a night
shift, so roughly 70% of what devclaw does is counted by nothing:
`problem_cycle_count` never reaches 2, `should_file` never fires, and the owner
does the triage by hand every morning.

Checked against the night of 2026-09-06→07 (window = 21:00–04:00 UTC in BST):
the env-deficiency block at 00:58Z and the `change_class` failure at 02:00Z
fall inside; the review-gate crash at 18:54Z, the lkc-23 contract block at
07:05Z and fs-431's CI-red at 06:39Z all fall outside and were counted by
nothing.

This is the same defect class as the three fixes shipped alongside it — a
mechanism bound to a proxy for the fact it cares about. "Has this problem
recurred across cycles?" was bound to a night window devclaw outgrew.

**Rejected alternative — widen the hardcoded window to 24h.** Correct today,
wrong the moment a run window is re-enabled, and it leaves two independent
sources of truth for "when does devclaw work". The knob IS the drift.

## Requirements

- **R1** The cycle window is derived from the stored run schedule: enabled ⇒
  its `start`/`end`/`tz`; disabled ⇒ the full calendar day in its `tz`.
- **R2** `most_recent_closed_window` stays a PURE function over
  `(now_ms, start, end, tz)`; the schedule read happens at the layer-2 call
  site. A full day is expressed as `start == end`, which the existing
  arithmetic already yields a 24h window for — no new date math.
- **R3** `DEVCLAW_RUN_CYCLE_START/END/TZ` are retired: one fact, one home
  (`config.py`'s single-doorway rule). The env-vars doc loses the rows in the
  same PR (`test_env_vars_doc_sync` enforces this).
- **R4** The report header states the window it actually covered, instead of
  rendering module constants that may not describe it.

## Plan

1. `cycle_report.py` — `cycle_window_for(schedule)` (pure); drop the module
   constants; `CycleReport` carries `window_label` for R4.
2. `service.py` — read `get_run_schedule()`, resolve, pass through.
3. `config.py` + `docs/reference/env-vars.md` — retire the three vars.
4. Tests — extend the cycle-report class test: a disabled schedule yields the
   calendar day, an enabled one yields its window, and a problem seen outside
   the old night window now counts toward filing.

## Tasks

- [x] T1 cycle_window_for + pure-function plumbing (R1, R2, R4)
- [x] T2 service call site reads the schedule (R1)
- [x] T3 retire the env knob + env-vars doc (R3)
- [x] T4 tests (R1, R4) + docs/INDEX currency

## Done when

- With the run window disabled, a problem seen at 14:00 counts toward its
  cycle and reaches `should_file` after two days.
- With a run window enabled, the cycle matches it exactly.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
