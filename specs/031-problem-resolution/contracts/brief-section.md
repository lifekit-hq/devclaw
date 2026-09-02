# Contract: the Decisions section of the advance brief

Rendered host-side by `devclaw/goal/decisions.py::render(rows)` — the sibling
of `prior_increments.render` — and slotted into the advance brief
(`devclaw/advance_brief.py`) directly after the prior-increments section.
Present on every dispatch of a goal that has at least one current Decision;
absent otherwise (unlike prior increments, absence needs no statement — "no
decisions" is the default state of a goal and saying so buys nothing).

## Shape

```
Decisions on this goal — settled by the owner, apply them as fact (2 current):
These are devclaw's own records of what the owner decided, not a worker's
summary. Do NOT re-derive, re-litigate, or ask about a decided clause; build
on it. A decision names the done_when clause it settles.
- [clause: "the capability scan sources from the project registry"] → correct_implementation: "walk every registered project's manifest, not live workspaces" (owner, 2026-09-02)
- [clause: "all tests pass"] → decide: rewritten as "no new failures relative to main" (admission, 2026-09-02)
```

- Head line is `DECISIONS_MARKER` (`"Decisions on this goal"`), so detectors
  and tests key off one constant, as with `PRIOR_INCREMENTS_MARKER`.
- One line per **current** Decision (superseded ones are never rendered),
  oldest first, each: clause (or `contract` when clause-level is empty) →
  verb: option label or text (provenance, date).
- Only devclaw-controlled fields: `clause`, `verb`, `option_key`→label,
  `text`, `provenance`, `made_at`. Never the worker's `Agent summary:` or
  any Problem `what` text that originated from a worker's own block reason
  verbatim — the Problem's `what` is stored, but the feed-forward carries the
  *Decision*, which is the owner's or the lint's (#358 trust boundary).
- Entry list is tail-kept under `prompt_budget.DECISIONS_KEEP` (same
  mechanism as `cap_prior_increments`; the marker line is never capped).

## Worker skill (one line, `runner/skills/_writes-code/`)

> A *Decisions on this goal* section in your brief is settled fact from the
> owner: apply it, never re-open it, and cite it in your hand-back for the
> clause it settles.

Plain markdown, one home (`runner/skills/`), baked into the sandbox image.
