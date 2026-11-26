# TinySpec: goals and decisions hold references to GitHub, not copies of its text

**Branch**: fm/iss-dc-1058
**Date**: 2026-11-26
**Status**: in review
**Complexity**: small (two dropped columns, one display module)
**Issue**: #1058

## North-star case

- **Failure moved**: *ran and produced garbage*. A second copy of the issue
  (`goals.objective`) or of the owner's comment (`decisions.text`) can drift
  from GitHub, and any path that reads the copy acts on stale text.
- **Number that shows it**: zero columns in `goals` / `decisions` that hold
  GitHub text (`tests/test_goal_layer_stays_readable.py` pins the column sets).
- **Cut when**: GitHub stops being the record from `ready` on.

## What

`goals` holds issue numbers and dispatch metadata only; `decisions` holds the
comment URL `decide` posted. The console and the MCP read tools fill a goal's
`objective` (its issues' titles) and a decision's `text` (its comment's body)
live in `devclaw/server/display.py` — cached per process, bounded per request
— and no tick, gate or merge path imports it.

## Requirements

1. Existing databases drop `goals.objective` and `decisions.text` on open
   (idempotent `ALTER TABLE … DROP COLUMN`); every row keeps working.
2. `create_goal` takes no `objective`; `decide` refuses when the comment was
   not posted (the URL is the record).
3. A cold console load never blocks on GitHub past `_BUDGET_S`: a missing
   title falls back to the goal id, and the next poll fills it.

## Rejected

- Keep the columns marked display-only: the goal row would still carry issue
  text that a later path could read as authority.
