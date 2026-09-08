# TinySpec: a merge reads the verdict of record — the local suite is not it

**Issue**: none (Denys, 2026-09-08 night — "how is this possible that we ship
something that breaks CI? It is not supposed to merge until everything is clean")
**Branch**: fix/merge-reads-the-verdict
**Date**: 2026-09-08
**Status**: done — implemented 2026-09-08
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. On 2026-09-08 two PRs (82f82ca,
  f30f61d) were squash-merged into `main` with a RED CI, and `main` stayed red
  for five hours. Spec 032 makes the project's CI rollup the verdict of record —
  devclaw reads its own repo's rollup before a done-check and again before
  merge-on-close — so a red `main` turns every devclaw-repo goal into a
  `mechanical:ci` hold the owner then has to reason about by hand.
- **Number that shows it**: merges landing on a non-green head — target zero.
  Today: 2 of the day's 4 merges.
- **Cut when**: GitHub branch protection with required status checks is enabled
  on `main` (see Platform note) — the platform then enforces it and this hook is
  redundant.

## Root cause

Spec 032 ruled that the project's own CI is the verdict of record, and wired it
into the **autonomous** path: `tick_donegate` reads the PR's rollup before the
done-check and refuses to merge-on-close on a non-green head. The **human path**
was never wired.

What the human path actually verifies, in `/ship`:

| Step | Verifies | On which machine |
|---|---|---|
| `pytest -q` | the suite | the dev box — quiet, fast, 16 cores |
| `ruff` / `mypy` / `lint-imports` | lint | the dev box |
| `gh pr create` | a North-star case exists (hook) | — |
| **`gh pr merge --squash`** | **nothing** | — |

So the ritual's evidence is a *local* run, and the merge step consults no
verdict at all. That is fine exactly as long as local and CI agree — and on
2026-09-08 they stopped agreeing, because CI runs on the deploy host and the
failure (tinyspec `suite-owns-its-environment`) was a lock race that only a
loaded box loses. Local was green, CI was red, and the merge asked neither.

The class: **a decision is placed where it cannot be enforced** (Denys,
2026-08-08 — an invariant belongs in code, not in a ritual). "Green before
merge" is stated in `.claude/rules/git-workflow.md` and in `/ship`, and lives
nowhere that can refuse. Every existing guard at this boundary is a PreToolUse
hook (`main-branch-guard`, `north-star-case-guard`); this is the third and last
unguarded step of the same ritual.

Rejected: enabling GitHub branch protection *instead* (correct, but see the
Platform note — it is unavailable on the current plan for these repos, and it
cannot be set from this session; recorded as the cut condition, not the fix);
making `/ship` poll CI before merging (a ritual instruction is what already
failed — the whole point is that the rule must refuse, not remind); blocking
`gh pr create` on CI (nothing has run yet at that boundary).

## Platform note (for Denys, not implemented here)

`gh api repos/lifekit-hq/<repo>/branches/main/protection` returns **403 "Upgrade
to GitHub Pro or make this repository public"** on the private lifekit repos, and
`main` on `lifekit-hq/devclaw` reports **no protection and no rulesets** — there
is nothing at the platform layer requiring a green check before merge, on any of
them. Enabling it (GitHub Team, or public repos) would make this hook redundant
and would also cover merges made from the GitHub web UI, which no local hook can
see. That is an account decision and is left to Denys.

## What

A PreToolUse guard, `.claude/hooks/merge-verdict-guard.py`, on the one boundary:

- **BLOCKED** `gh pr merge` when the PR's CI rollup is not green — any check
  `FAILURE`/`TIMED_OUT`/`CANCELLED`/`ACTION_REQUIRED`, or still `PENDING`.
- **ALLOWED** every merge whose checks are all green; any repo with no checks
  configured; a rollup that cannot be read (fail-OPEN, like every hook here — a
  network hiccup must never wedge the ritual, and the human is present).
- **Escape hatch** `DEVCLAW_ALLOW_RED_MERGE=1`, mirroring `DEVCLAW_ALLOW_MAIN`
  and `DEVCLAW_ALLOW_NO_CASE`: a deliberate red merge stays possible and stays
  a conscious act that names itself.

## Context

| File | Role |
|------|------|
| `.claude/hooks/merge-verdict-guard.py` | New — the guard |
| `.claude/settings.json` | Modified — wire it into the existing Bash PreToolUse list |
| `.claude/rules/git-workflow.md` | Modified — the rule now names the hook that enforces it |
| `CLAUDE.md` | Modified — the dev-harness paragraph lists the third guard |

## Requirements

1. `gh pr merge` on a PR with a failing or pending check is refused.
2. The refusal names the failing checks and the escape hatch.
3. A green PR merges unchanged; a repo with no checks merges unchanged.
4. The hook fails OPEN on any error (no network, no `gh`, unparseable payload).

## Plan

Copy the shape of `north-star-case-guard.py`: parse the PreToolUse payload,
match the command, consult `gh pr checks --json`, print to stderr and exit 2 to
block.

## Tasks

- [x] `.claude/hooks/merge-verdict-guard.py`
- [x] Wire into `.claude/settings.json`
- [x] `.claude/rules/git-workflow.md` + `CLAUDE.md` name the guard
- [x] Full suite + `ruff check .` + `mypy` + `lint-imports` green

## Done when

- `gh pr merge <red PR>` is refused with the failing check named.
- No merge lands on a non-green head without `DEVCLAW_ALLOW_RED_MERGE=1`.
