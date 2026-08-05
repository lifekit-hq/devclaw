# PLAN.md — your durable working memory

This goal outlives any single session. You may be picking it up from an earlier session (perhaps one that crashed mid-flight), and a later session — maybe not you — picks it up from here. `PLAN.md` at the repo root is how those sessions hand off: the plan and reasoning that would otherwise die when your session ends. It is FOR the work, not a deliverable of it, and nothing outside the repo parses it — so keep it a tight index of your thinking, not an essay. Own it: read it first, keep it honest, commit it with your change.

## Start of session: read, then verify against the repo

1. If `PLAN.md` exists, read it — it is your prior self's handoff. Trust it as the starting point, but the **code is the source of truth**: where the repo and `PLAN.md` disagree, the repo wins and you fix `PLAN.md` (a session can die between changing code and updating the plan).
2. If it does not exist, plan from the ground truth you have — the goal, `AGENTS.md`, the repo itself — and create it. A missing plan is "plan from scratch this session," never a reason to stop.

Don't wait to be handed context; pull what you need from these durable places the way you'd explore a codebase you were briefed on and told "go look."

## The shape

Four sections, headers as written — gist-and-point, not prose:

```
## Destination
<what "done" looks like for this goal, in a sentence or two>

## Decisions so far
- <load-bearing choice already made> — <one-line why>

## Next / open questions
- <the immediate frontier: what to do next, what's still unresolved>

## Out of scope
- <ruled-out work, so the frontier stays honest>
```

## As you work

- When you make a real, load-bearing choice (a stack, a schema, an API shape, a trade-off), record it under **Decisions so far** with its one-line reason — so no future session relitigates it.
- Move an item from **Next / open questions** to **Decisions so far** the moment you resolve it; add new questions as investigation surfaces them.
- Keep it proportionate to the goal: a broad multi-session goal earns a real plan; a narrow one-off earns a few lines. Never pad it.

## Rules

- The code is the source of truth; `PLAN.md` is an accelerator, not the authority. Never gate your work on it being perfect.
- Don't restate what `AGENTS.md` or the code already says — point to it. One decision lives in one place.
- Don't erase a decision to tidy up. If you reverse one, keep the old line and add the reversal with its reason — a deleted decision is a lost decision, and the next session will re-make the mistake it warned against.
- Commit `PLAN.md` in the same commit as the change it describes (the commit-hygiene step below stages everything). It travels with the work on the delivery branch.
