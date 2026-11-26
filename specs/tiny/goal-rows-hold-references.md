# TinySpec: goals and decisions point at GitHub; their text copies are display-only

**Branch**: fm/iss-dc-1058
**Date**: 2026-11-26
**Status**: in review
**Complexity**: small (one display module, two guards, no schema change)
**Issue**: #1058

## North-star case

- **Failure moved**: *ran and produced garbage*. `goals.objective` repeats
  issue text and `decisions.text` repeats the owner's comment; a path that
  read either copy would act on text that can drift from GitHub.
- **Number that shows it**: zero reads of the copies on a decision path
  (`tests/test_goal_layer_stays_readable.py` pins it: a static scan of the
  goal, queue and engine layers, and a brief built from a goal row whose
  objective disagrees with its issue).
- **Cut when**: GitHub stops being the record from `ready` on.

## What

The console and the MCP read tools show a goal's title from its issues and a
decision's text from its `comment_url`, read live in
`devclaw/server/display.py` (cached per process, bounded per request; the
stored copy stands in only until the live read arrives). `goals.objective`
and `decisions.text` stay in the schema, marked display-only in code and docs.
No stored data is removed.

## Requirements

1. No tick, gate, merge, settle or engine path reads `.objective` or lists
   decisions; the contract is the issue's live `## Done when`.
2. A cold console load never blocks on GitHub past `_BUDGET_S`.
3. Existing goals and decisions keep working: no migration, no dropped column.

## Rejected

- Drop both columns with a migration: irreversible once deployed, and the
  issue allows display-only. Revisit once the display path has run a while.
