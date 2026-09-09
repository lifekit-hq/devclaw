# Durable memory — the speckit artifacts, never a PLAN.md

Work outlives your session: you may be picking it up, and a later session
picks up from you. The handoff is the repo's **speckit artifacts**:
`specs/<feature>/` (spec.md, plan.md, tasks.md). They are the plan of record;
the code is the source of truth — where they disagree, the repo wins and you
fix the artifacts.

**Never create or update a root `PLAN.md`** — retired; a stale one is left
untouched, removing it is not your task.

## Start of session

1. **Your feature is the one this branch added** —
   `git diff --name-only --diff-filter=A origin/HEAD...HEAD -- specs/`.
   Read its spec.md and tasks.md first: your prior self's handoff. A feature
   already on the default branch is another goal's — never adopt one. Missing
   plan.md or tasks.md? Run plan → tasks into it before implementing.
2. Nothing added and the repo has `.specify/`? Create it with
   `create-new-feature.sh --timestamp --short-name <slug>`, then specify →
   plan → tasks. `--timestamp` is not optional: the `NNN-` allocator reads only
   the `specs/` this checkout can see, so concurrent goals all mint the same
   number. Never pass an issue number as the feature number — wrong namespace
   and wrong sort.
3. No `.specify/` (a plain repo, a one-shot fix)? Just do the task well — no
   planning file is expected of you.

## As you work

- Flip `- [ ]` → `- [x]` in `tasks.md` as each task lands — that is how the
  next session sees what's left without re-deriving it.
- Implement only the smallest not-yet-done story-slice (`[US<n>]`); one
  coherent slice = one reviewable PR, this session's whole scope. Never build
  ahead — the harness ends the session once a completed slice is left behind.
  Land the slice (tasks.md honest, artifacts committed, checks run) first.
- Record load-bearing choices (a stack, a schema, an API shape) with a
  one-line why in plan.md, so no session relitigates them.
- While planning (the tasks step), record for EACH story-slice one line in
  plan.md naming the files/areas it touches and any constraint discovered —
  the next session's read budget.
- When implementing a slice, read its plan.md line and AGENTS.md FIRST and
  explore raw files only within the slice's declared surface. If the line is
  stale or missing, fix it first and say so in the commit — never silently
  fall back to repo-wide exploration.
- Commit the artifact changes together with the code they describe.
- *Decisions on this goal* in the brief are the owner's rulings: apply, never re-open.
