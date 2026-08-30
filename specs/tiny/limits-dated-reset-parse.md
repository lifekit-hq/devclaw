# TinySpec: Parse date-carrying usage-limit reset wording

**Branch**: fix/limits-dated-reset
**Date**: 2026-08-30
**Status**: done
**Complexity**: small

## What

The 2026-08-29 night run hit the long "extra usage" cap and Claude's error stated
`resets Aug 31, 6am (UTC)` — a **date + time**. `_RESET_AT_ABS` only knows the
time-only wording (`resets 10pm (UTC)`), so the stated reset was dropped,
`stated=False`, and the loop rode the 30-min default re-probe: doomed probe →
re-pause → ping-pair spam, until `MAX_PAUSE_REQUEUES=5` failed 3 tasks. Fix the
parser to honor the dated wording, and raise the stated-hint trust cap so a
multi-day reset (weekly cap) is honored instead of clamped to 24h.

## Context

| File | Role |
|------|------|
| `devclaw/loom/limits.py` | Will be modified — `_RESET_AT_ABS` regex, `_seconds_until_reset`, `RATE_LIMIT_STATED_MAX_S` |
| `tests/test_limits.py` | Will be modified — extend the existing class tests (`test_seconds_until_reset` parametrize, classify tests) with the dated wording |
| `runner/runner.py` | Context only — its vendored copy intentionally parses no absolute times (host re-classifies from original wording); NOT touched |
| `devclaw/queue/settle.py` | Context only — consumes `classify_failure(now_utc=…)` + `pause_seconds(stated=…)`; no change |

## Requirements

1. `_seconds_until_reset("… resets Aug 31, 6am (UTC)", now)` returns seconds to
   Aug 31 06:00 UTC + slack (not `None`). Month name may be abbreviated (`Aug`)
   or full (`August`); comma optional; time-only wording keeps working byte-identically.
2. A stated date that already passed (clock skew / message read late) rolls to
   the next year only across a year boundary (e.g. `resets Jan 2` seen Dec 31);
   an invalid date (e.g. `Feb 30`) degrades to `None`, never raises.
3. `classify_failure` on the live 2026-08-29 string with `now_utc` injected
   yields `QUOTA`, `stated=True`, and a `retry_after_s` reaching Aug 31 06:00 UTC.
4. `RATE_LIMIT_STATED_MAX_S` is raised from 86_400 (24h) to 7 days (604_800) so
   a stated weekly-cap reset is trusted in full; the unstated default/cap
   (1800s / 3600s) is untouched. Rationale recorded in the constant's comment:
   the bound is distrust-of-parse, not policy — a stated hint can never exceed
   the weekly cycle, so 7d is the natural bound.
5. Zero behavior change for every existing wording: the full existing
   `tests/test_limits.py` passes unmodified except for added parametrize cases
   and the STATED_MAX constant's new value.

## Plan

1. Extend `_RESET_AT_ABS` with an optional month-day prefix:
   `reset[s]?(?:\s+at|\s+on)?\s+(?:([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+)?<existing time part>`.
2. In `_seconds_until_reset`, when month+day captured: map month name → number
   (first-3-letters lookup, reject unknown → `None`), build the target datetime
   in `now_utc`'s year (ValueError → `None`), roll +1 year if target ≤ now;
   date-absent path unchanged (roll +1 day). Same `_RESET_ABS_SLACK_S` slack.
3. Bump `RATE_LIMIT_STATED_MAX_S` to `7 * 86_400` with the rationale comment.
4. Extend the existing parametrized cases in `tests/test_limits.py`:
   dated wording (abbrev + full month, with/without comma), the exact live
   2026-08-29 string through `classify_failure`, year rollover, invalid-date →
   `None`, and a stated ~35h hint surviving `pause_seconds` uncapped.

## Tasks

- [x] Extend `_RESET_AT_ABS` + `_seconds_until_reset` for the dated wording
- [x] Raise `RATE_LIMIT_STATED_MAX_S` to 7 days with rationale comment
- [x] Extend `tests/test_limits.py` class tests (parametrize cases, no new sibling tests)
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] The live 2026-08-29 string yields a stated pause until Aug 31 06:00 UTC (+slack)
- [x] Full suite, ruff, mypy green; PR open (squash lane)
