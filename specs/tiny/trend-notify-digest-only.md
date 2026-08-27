# TinySpec: trends never ping the owner — the digest is the one human surface

**Issue**: none (ruled in-session by Denys 2026-08-27: "I just don't get why I
get those messages in the TG channel all the time")
**Branch**: fix/trend-notify-digest-only
**Date**: 2026-08-27
**Status**: done
**Complexity**: small

## What

Remove the trend detector's owner-notification channel entirely. Trend
observations are machine-side records (`trends.md`, read via `review_trends`);
their one human surface is the status digest (`/devclaw-status`), where the
read is deliberate. No Telegram pings for trends, at any altitude.

## Context

The 2026-08-24 ruling said benign trends stop pinging and actionable ones
surface in the digest. PR #678 implemented only half: it gated the ping on the
verdict, so **actionable** trends (with a `proposed_action`) kept going to
Telegram. Denys ruled 2026-08-27 that the channel goes entirely.

| File | Role |
|------|------|
| `devclaw/trend_detector.py` | `_fire`'s notify tail + `notifier_send` ctor param + `NotifierSend` type — removed |
| `devclaw/goal/service.py` | the fire-and-forget `_notify_send` shim — removed |
| `tests/test_trend_detector.py` | notifier fixture removed; fire assertions re-grounded on trends.md content / LLM-call counts |

The trend-detector *discipline* stays under its existing measurement rule (the
digest section is the measurement: if weeks pass and no trend changes a
decision, retire the detector). This spec changes only the delivery channel.

## Requirements

- [x] `TrendDetector` has no notification channel: no `notifier_send`
  parameter, no owner ping on any verdict.
- [x] Actionable and benign fires alike are recorded to `trends.md` with
  cooldown + fingerprint dedup unchanged.
- [x] Named regression:
  `test_actionable_trend_is_recorded_but_owner_is_never_pinged` (records the
  actionable entry; asserts structurally that no notifier parameter exists).
- [x] Zero-token and cognition behavior byte-unchanged (one LLM call per fire,
  gates untouched).

## Tasks

- [x] Remove notify tail + ctor param + type alias in `trend_detector.py`
- [x] Remove the `_notify_send` shim in `goal/service.py`
- [x] Rewrite tests off the notifier; add the named regression
- [x] Full suite (2506 passed) + `ruff` + `mypy` green

## Done When

- No code path can deliver a trend observation to the owner notifier; the
  digest reads `trends.md` as before; suite green with the named regression.
