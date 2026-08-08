# PLAN.md — your durable working memory

This goal outlives any single session — you may be picking it up from a prior session (perhaps one that crashed), and a later session picks up from you. `PLAN.md` at the repo root is that handoff: the plan and reasoning that would otherwise die with your session. It is FOR the work, not a deliverable; nothing outside the repo parses it — keep it a tight index, not an essay. Read it first, keep it honest, commit it with your change.

## Start of session

1. If `PLAN.md` exists, read it — your prior self's handoff. Trust it as the start, but the **code is the source of truth**: where they disagree, the repo wins and you fix the plan.
2. If not, create it from the ground truth you have — the goal, `AGENTS.md`, the repo itself. A missing plan means "plan from scratch," never "stop." Pull what you need; don't wait to be handed context.

## Shape — size the plan to the goal

A small, well-understood goal earns a few lines (Destination + a couple of Decisions + a short task list — skip milestones); a large, multi-session goal earns the full shape. Never pad, never wing it.

```
## Destination                   — what "done" looks like (the acceptance)
## Decisions so far              — load-bearing choices, one-line why each
## Milestones                    — large goals: the coarse plan to done ( - [ ] / - [x] )
## Tasks — <current milestone>   — the frontier, broken down ( - [ ] / - [x] )
## Out of scope                  — ruled-out work, so the frontier stays honest
```

## Planning a large goal — rolling wave

You can't plan a large goal to the last task up front, so don't. Lay out the **Milestones** coarsely, all the way to the Destination; **order them to de-risk early** — riskiest architectural bet first, as a thin spike reality can validate cheaply before everything piles on top. Break a milestone into concrete **Tasks** only when you reach it; research the load-bearing bets at that altitude, not milestone 8 yet.

## As you work

- Flip `- [ ]` → `- [x]` as each task lands — how you and the next session see what's left **without re-deriving it**.
- Finish a milestone's tasks → mark it `- [x]`, then break down the next. **One advance session closes ONE milestone, shipped as one reviewable PR — never build ahead and flip several `- [ ]` → `- [x]` at once.**
- Record load-bearing choices (a stack, a schema, an API shape) under **Decisions so far** with their one-line reason, so no session relitigates them.
- **The goal is done only when every milestone is checked and the Destination is met — not when your one increment is done.**

## Rules

- Code is the source of truth; `PLAN.md` is an accelerator, not the authority — never gate work on it being perfect.
- Don't restate what `AGENTS.md` or the code already says — point to it. One decision lives in one place.
- Don't erase a decision to tidy up. If you reverse one, keep the old line and add the reversal with its reason — a deleted decision is a lost decision the next session re-makes.
- Commit `PLAN.md` in the same commit as the change it describes; it travels with the work on the delivery branch.
