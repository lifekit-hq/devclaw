# The plan lives in the repo — speckit

Work outlives your session. The handoff is the repo's **speckit artifacts** under `specs/<feature>/` (spec.md, plan.md, tasks.md); the code is the source of truth and the artifacts follow it.

- No `.specify/` in the repo? Copy `/opt/devclaw/specify` to `.specify/` and commit it.
- Your feature is the one this branch added: `git diff --name-only --diff-filter=A origin/HEAD...HEAD -- specs/`. None yet? Create one with `.specify/scripts/bash/create-new-feature.sh --timestamp --short-name <slug>`, then specify → clarify (take sensible defaults; a real scope question is a BLOCKED line, not a guess) → plan → tasks, and commit them.
- Take the smallest not-yet-done task from tasks.md; flip `- [ ]` → `- [x]` as it lands; one coherent reviewable increment per session.
- Record load-bearing choices (a stack, a schema, an API shape) with a one-line why in plan.md so no session relitigates them.
- Never create a root `PLAN.md`.
