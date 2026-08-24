# TinySpec: Trend notification gated on the LLM verdict

**Branch**: fix/trend-notify-gate
**Date**: 2026-08-24
**Status**: implemented
**Complexity**: small

## What

Stop pinging the owner for trend observations the detector itself judged
benign. `TrendDetector._fire` currently notifies unconditionally; after this
change it notifies only when the retrospective's `proposed_action` is
non-null. Benign entries still land in `trends.md` (the durable record read
back by `review_trends` and the detector's own dedup context) — they just
stop interrupting the owner.

## Context

Live evidence 2026-08-24: 2 of 3 trend Telegram pings that evening were
self-declared-benign "(none)" reports (D1/D3 cold-start scaffolding artifacts
on the shakedown-bench workspace). The detector's own prompt mandates
`proposed_action` MUST be null for benign patterns — the verdict exists, it
just doesn't own the notification altitude.

| File | Role |
|------|------|
| `devclaw/trend_detector.py` | Will be modified — gate the `self._notify(...)` call in `_fire` on `entry["proposed_action"]` |
| `tests/test_trend_detector.py` | Will be modified — add the named regression test |

## Requirements

1. A fired signal whose parsed entry has `proposed_action == None` writes its
   `trends.md` entry, sets cooldown + fingerprint (and bookmark where
   applicable), and sends NO notification.
2. A fired signal with a non-null `proposed_action` notifies exactly as
   before (payload shape unchanged).
3. No change to cooldown/fingerprint/bookmark semantics — a benign fire still
   suppresses identical re-fires.

## Plan

1. In `_fire` (devclaw/trend_detector.py), wrap the `self._notify(...)` block
   in `if entry.get("proposed_action"):` — `_parse_entry` already normalizes
   empty/"null"/"None" to `None`, so truthiness is the correct check.
2. Add named regression test
   `test_benign_verdict_writes_entry_but_does_not_notify`: caller payload with
   `proposed_action: None` → trends.md entry exists, `sent == []`, cooldown +
   fingerprint set. Companion assertion path (actionable → notifies) is
   already covered by `test_per_goal_fire_writes_entry_and_sets_cooldown`.

## Tasks

- [x] Gate `self._notify` in `_fire` on non-null `proposed_action`
- [x] Add `test_benign_verdict_writes_entry_but_does_not_notify`
- [x] Full suite + ruff + mypy green

## Done When

- [x] All tasks checked off
- [x] Suite, ruff, mypy pass
- [x] PR open (squash-merge target: main)
