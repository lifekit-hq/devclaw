# Spec 026 — Dispatch brief budget

**Issue:** lifekit-hq/devclaw#729  
**Parent class:** #707 (worker-context overflow)

## What

The goal dispatch brief grows without bound. Spec 021 dieted what the worker
READS once in-sandbox; it never touched what the worker is HANDED at dispatch.

Two sections of the advance brief accumulate without a size cap:

1. **Steering** — every unread `goal_steering` row is joined verbatim and
   appended. A later steering line typically CORRECTS an earlier one; replaying
   all of them grows the brief monotonically and the operator's primary
   correction verb makes overflow more likely.
2. **Prior increments** — already capped by `PRIOR_INCREMENTS_KEEP`; this spec
   does not change it.

Evidence (both after the 021 deploy on 2026-08-28, sandbox rebuilt):

| time (UTC) | task | outcome |
|---|---|---|
| 11:36 | `0db1017c` | `prompt is too long` |
| 13:18 | `61784fcd` | `prompt is too long` |

`devclaw-022-one-lane-2026-08-27` rendered `[+6 steering line(s), +6 prior increment(s)]`
at the failing dispatches.

## Requirements

- **R1** — The steering section of the advance brief is bounded by an explicit
  named constant (`STEERING_KEEP`). The newest steering line always survives
  (tail-keep semantics); older lines are compacted behind a truncation marker.
- **R2** — The rendered brief's character count is recorded in the goal log at
  every advance dispatch, readable from the goal's own telemetry.
- **R3** — The brief character count rides the `DispatchEvent` trace record.
- **R4** — Compaction is pure string/DB mechanism: zero LLM calls on the tick
  path. The zero-token idle guard tests (`FakeClaude.calls == 0`) stay green
  and are not edited to pass.
- **R5** — The newest steering line is never the content dropped; correction
  must survive compaction intact.

## Success criteria

- SC-001: A brief built from 6 steering lines of 500 chars each stays under
  `STEERING_KEEP + overhead`; the most recent line is byte-present.
- SC-002: A brief built from 6-steer/6-increment adversarial history is bounded
  (under `PRIOR_INCREMENTS_KEEP + STEERING_KEEP + 20_000` chars).
- SC-003: After an advance dispatch, `store.recent_log(goal_id)` contains a
  line matching `brief:.*chars`.
- SC-004: The zero-token guard tests (`FakeClaude.calls == 0`) remain green.

## Out of scope

- Re-opening spec 021's in-session worker context budget.
- Dispatch-boundary slice-guard wedge (#728) and freshness-guard deadlock (#726).
- LLM-based summarization of steering on the tick path.
- Total brief hard-cap (the section caps are the mechanism).

## Rejected alternatives

- **LLM summarization of old steering** — forbidden by the zero-token guard on
  the tick path.
- **Deleting consumed rows from the DB** — rows are the audit log; deletion
  would break the inbox.md view migration.

## Implementation plan

See plan.md.
