# When a repo mechanism forces a change your ticket forbids

A repo-local **mechanism** — a pre-commit hook, a lint autofix, a formatter, a
version-bump gate, a CI check that rewrites files — sometimes mechanically
forces a change your ticket **explicitly forbids** (classic: the ticket says
"do NOT touch `package.json`" but a pre-commit version-gate bumps it on every
commit). Two moves lose, and you must take neither:

- **Appease it** — make the forbidden change so the gate passes. That ships a
  scope violation; review flags it and the task fails. Never let a hook's
  autofix stage the forbidden edit for you either.
- **Fight it blindly** — retry the same commit hoping the gate relents. It
  won't; the task dead-ends.

## Fix the mechanism, don't obey it

The gate is the **cause**, not a fact of nature — so **fixing or relaxing it is
in scope** for this change, like refactoring what you touch is in scope.

1. **Diagnose** the mechanism forcing the edit: `.pre-commit-config.yaml`,
   `.husky/`, a `lint-staged` block, a `core.hooksPath` hook, a `Makefile`
   target, a CI step. Name it in your summary.
2. **Fix it minimally** — correct the over-broad rule, or skip it for this one
   commit with a reason (`--no-verify`, `SKIP=<hook-id>`). Prefer the real fix;
   bypass only a legitimate gate that just shouldn't apply here. Never disable a
   repo's safety net wholesale or weaken a check to go green.
3. **Document WHY** in the commit body and in `AGENTS.md` (if the repo has
   one), so the next task doesn't re-hit it. A documented bypass reads as
   resolving a real conflict; a silent one reads as sneaking past a gate.

If the mechanism genuinely can't be touched, **do not appease and do not
fabricate success** — report it as a blocker: name the mechanism, the forbidden
change it forces, and why you couldn't resolve it. A loud "this hook and this
ticket contradict" is a correct outcome; a silent forbidden edit is not.
