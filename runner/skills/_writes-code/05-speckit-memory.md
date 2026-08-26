# Durable memory — the speckit artifacts, never a PLAN.md

Work can outlive your session — you may be picking it up from a prior session,
and a later session picks up from you. The handoff is the repo's **speckit
artifacts**: `specs/NNN-*/` (spec.md, plan.md, tasks.md). They are the plan of
record; the code is the source of truth — where they disagree, the repo wins
and you fix the artifacts.

**Never create or update a root `PLAN.md`.** It is retired as a planning spine;
everything it used to carry lives in the feature's speckit artifacts instead.
If a stale root `PLAN.md` exists, leave it untouched — removing it is not your
task.

## Start of session

1. Find the current feature: the smallest not-yet-complete `specs/NNN-*/`
   (its `tasks.md` still has unchecked items). Read its spec.md and tasks.md
   first — that is your prior self's handoff.
2. No `specs/` and the repo has `.specify/`? Create the feature with
   `.specify/scripts/bash/create-new-feature.sh` and run the speckit steps
   (specify → plan → tasks) before implementing.
3. No `.specify/` at all (a plain repo, a bounded one-shot fix)? Just do the
   task well — no planning file of any kind is expected of you.

## As you work

- Flip `- [ ]` → `- [x]` in the feature's `tasks.md` as each task lands — that
  is how the next session sees what's left without re-deriving it.
- Implement only the smallest not-yet-done story-slice (`[US<n>]`); one
  coherent slice = one reviewable PR. Never build ahead into later stories —
  one slice is this session's whole scope, and the harness ends the session
  once a completed slice is left behind for the next one. Land the slice
  (tasks.md honest, artifacts committed, checks run) before touching anything
  else; the next slice belongs to the next session.
- Record load-bearing choices (a stack, a schema, an API shape) with a
  one-line why in the feature's plan.md, so no session relitigates them.
- Commit the `specs/NNN-*/` artifact changes together with the code they
  describe.
