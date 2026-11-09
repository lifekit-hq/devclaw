# TinySpec: the done-gate reads a valid verdict through the prose around it

**Branch**: goal/dc-verdict-parse-1013
**Date**: 2026-11-09
**Status**: in review
**Complexity**: small (one parser in `devclaw/goal/donegate.py`, one test)
**Issue**: #1013 — the done-gate answers "unreadable verdict" on a verdict that
is valid, because text with a brace sits around it

## North-star case

- **Failure moved**: *ran but needed the owner*. An unreadable verdict is a
  devclaw defect that fails closed: the goal stops with "the review produced no
  readable verdict" and only an owner comment wakes it, even though the
  reviewer answered and the checks were green.
- **Number that shows it**: unreadable verdicts per done-gate review. PR 1012
  is one, on a run that was otherwise clean.
- **Cut when**: the reviewer's answer stops being free text — a structured
  channel for the verdict (a tool call, a file the session writes) would make
  the extraction moot.

## What

`parse_verdict` extracted the verdict in two steps: the last ` ```json `
fenced block, else — as a last resort — the slice from the first `{` to the
last `}` of the WHOLE message. On PR 1012 the reviewer wrote prose containing
braces around an unfenced verdict, so that slice was two things at once and
`json.loads` answered `Extra data`.

The verdict is now read as the **last JSON object in the message that carries a
`clauses` list**, fenced or bare, with the text around it ignored. Scanning
with `json.JSONDecoder().raw_decode` at each `{`/`[` makes "where does the
value end" the decoder's answer instead of a regex's guess, and makes the
fence incidental: a fenced verdict is simply a bare one with prose around it.
The scan lives inside `parse_verdict` rather than in a helper — the goal-layer
ceiling (pillar 1) was at exactly 1,500 lines, so the fix had to land net-zero
and a second `def` is five lines it has no budget for.

The mechanical rules are unchanged — no JSON, a non-object, or no clauses is
unreadable; `achieved` still needs every clause satisfied with non-empty
evidence and no `question`. The prompt still asks for one fenced block and
nothing after it (`devclaw/prompts/done-gate.md`); the parser no longer turns
a reviewer's deviation from it into a stop.

## Context

| File | Role |
|---|---|
| `devclaw/goal/donegate.py` | `parse_verdict` — the `raw_decode` scan over the message, then the mechanical validation (unchanged) |
| `devclaw/prompts/done-gate.md` | The format the reviewer is asked for — unchanged; the parser is the forgiving half |
| `tests/test_goal_tick.py` | `test_the_verdict_claim_never_beats_the_clauses` — the one home for parse_verdict's tripwire cases |

## Requirements

- Prose with braces before AND after a valid bare verdict object: the verdict
  is read (achieved or not, as its clauses say), never unreadable.
- Two fenced blocks: the last valid verdict wins.
- A bare verdict object followed by trailing text, or by a second JSON object
  that is not a verdict: the verdict is read.
- A brace in prose that is not JSON (`{Done when}`, `{a, b}`) is skipped, not
  a parse failure.
- Still unreadable, fail-closed: no JSON at all (`no JSON verdict in the
  review`), a JSON non-object (`verdict is not an object`), an object with no
  usable clauses (`verdict names no clauses`).

## Rejected alternatives

- **Keep the fence-first order and only fix the bare fallback.** The bug is
  the same class in either branch — "guess the extent of the value with a
  regex". One scan that the decoder bounds removes it from both.
- **Take the FIRST object with clauses.** A reviewer who corrects itself puts
  the real verdict last; the earlier object is the draft or an example of the
  schema. The issue rules last-wins.
- **Accept any last JSON object and report "names no clauses" for it.** A
  trailing non-verdict object (a status blob, a tool echo) would shadow the
  real verdict — the #1012 failure in a new shape. Requiring a `clauses` list
  makes the candidate test the verdict's own shape.
- **Loosen the prompt instead** (ask for a bare object, or for a marker pair).
  It costs a session nothing to be sloppy, and the host still has to parse
  whatever arrives; the fence instruction stays as the standard and the parser
  stops being brittle. Software owns the protocol (pillar 2).

## Tasks

- [x] `parse_verdict` scans with `raw_decode` and keeps the LAST object
      carrying a `clauses` list; nested values never surface
- [x] The three unreadable reasons preserved, each reachable
- [x] `test_the_verdict_claim_never_beats_the_clauses` extended with the noisy
      cases (existing assertions unchanged)

## Done When

- [x] `.devclaw/verify` green (pytest, ruff, mypy, lint-imports, yamllint,
      frontend lint/test/build/chrome-check)
- [x] Nothing under `devclaw/` changed but `donegate.py`, and the goal layer
      is back at 154 lines for the file / 1,500 for the layer
- [ ] This PR's CI green
