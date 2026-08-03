# Maintain the wayfinder plan-map

This goal is tracked as a **wayfinder map** on the target repo's GitHub issue tracker: one index issue plus one child issue per open decision. You OWN this map — the control plane reads it to decide what to dispatch next and when the goal is done. Keep it accurate as you work, using `gh`. Plan by charting the map, not by writing an essay.

## The exact format — a machine parses this, match it precisely

**The map index** — exactly one issue labelled `wayfinder:map`. Its body carries these sections, with these headers verbatim:

```
## Destination
<what "done" looks like — the goal, in a sentence or two>

## Notes
<durable context/preferences the next session needs>

## Decisions so far
- #<n> <ticket title>: <one-line gist of its resolution>

## Out of scope
- <ruled-out work, so the frontier stays honest>
```

**Decision tickets** — one child issue per open question whose answer is a DECISION (not a slice of build-work to grind). Label each `wayfinder:<kind>`, kind ∈ {`research`, `prototype`, `grilling`, `task`}. In the body:

- state the question sharply;
- declare each dependency on its own line: `Blocked by #<n>`;
- when resolved, add a line `Resolution: <the answer>` and **close** the issue.

## At the start of a session

1. `gh issue list --label wayfinder:map --state all` — does the map exist?
2. **If not, chart it** (breadth-first — map the frontier, don't solve it yet): create the `wayfinder:map` index with the Destination drawn from the goal, then a child decision ticket for each open question you must resolve to reach it, wiring `Blocked by #<n>` where one decision depends on another.
3. **If it exists, read it**: the index plus the open `wayfinder:*` tickets ARE your plan. Don't restate it — it's an index; each decision lives in its own ticket.

## Working the frontier

- Take the lowest-numbered OPEN ticket whose every `Blocked by` is CLOSED.
- Resolve it — research, prototype, decide, or implement, per its kind — to a real, defensible answer.
- Post `Resolution: <answer>`, `gh issue close` it, then update the index's **Decisions so far** with a one-line gist + link.
- If resolving it exposes new decisions, add new tickets (graduate the fog into the frontier).
- When every ticket is closed, the Destination is met — stop; the control plane's grounded done-gate takes it from there.

## Rules

- Index, not store: gist and link; never restate a decision that lives in its ticket.
- One `Blocked by #<n>` per dependency — the control plane parses these lines to know what is dispatchable; freehand prose deps are invisible to it.
- Never delete a ticket to tidy up — close it with a `Resolution:`, or move it to **Out of scope** on the index. A deleted decision is a lost decision.
