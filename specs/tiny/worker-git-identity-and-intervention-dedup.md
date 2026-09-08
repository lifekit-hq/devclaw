# TinySpec: the worker commits as devclaw again, and a hand commit is counted once

**Issue**: none (Denys, 2026-09-08 — "fix what is garbage and what is broken" after the spec 039 live audit)
**Branch**: fix/worker-git-identity-and-intervention-dedup
**Date**: 2026-09-08
**Status**: done
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. The number that measures it — interventions per achieved goal — is unreadable: `non_worker_commits` reported 66 hand commits this week, 43 distinct shas, and of eight sampled on GitHub four were the worker's own commits authored as `agent@devclaw`, `agent@lifekit-hq.local`, `devclaw@lifekit.local`. A metric that counts the worker's work as the owner's hand cannot say whether the loop runs unattended.
- **Number that shows it**: `interventions.non_worker_commits` on `get_scorecard_metrics` (66 on 2026-09-08 over 13 achieved goals; `per_achieved_goal` 6.92). After: only commits by an identity other than `devclaw/git_identity.py`'s, each once.
- **Cut when**: the ACP agent reports the author of every commit it makes, so the runner no longer has to carry an identity into it.

## What

Two defects on one metric, one root each, plus two honesty fixes on the same
surface:

1. **The worker's git identity never reached the agent.** The engine pins
   author + committer on the container env (`git_identity.py`, 2026-07-28),
   the compose file forwards the dials (tinyspec
   `compose-git-identity-passthrough`, 2026-09-06) — and the runner's agent
   env is an explicit allowlist that dropped all four `GIT_*` vars. Inside
   the agent git had no identity, refused to commit, and the model invented
   one per session. Every downstream reader of "who authored this" (the
   scorecard's non-worker-commit count, the owner's glance at a PR) was
   wrong from the day the allowlist landed.
2. **A hand commit was counted once per later task.** Delivery re-scans
   `origin/main..HEAD` at every settle and the settle records every foreign
   sha again. 66 rows for 43 shas.
3. Two counts of "the owner's steers" on one scorecard disagreed
   (`steering.human_steers` 14 from `goal_steering` rows, which `decide`
   also writes; `interventions.steers` 7). One definition survives.
4. The usage block printed a dollar figure while its own note said that
   figure is null under OAuth. The number is the CLI's API-equivalent
   estimate; the note now says so.

## Context

| File | Role |
|------|------|
| `runner/runner.py` | Modified — the four `GIT_AUTHOR_*`/`GIT_COMMITTER_*` vars cross the allowlist beside the credential registry (identity is a fact the environment carries, not a credential) |
| `devclaw/goal/state.py` | Modified — on a DB without the key, bootstrap PURGES every commit row (none was recorded against a real identity) THEN creates the partial unique index `uq_goal_interventions_commit (goal_id, ref) WHERE verb='commit'`; gated on the key's absence so it runs once and never on a keyed ledger |
| `devclaw/goal/state_interventions.py` | Modified — `INSERT OR IGNORE`; verbs stay append-only, only commits are keyed |
| `devclaw/doctor/checks_instance.py` | Modified — `instance.scorecard.goal_interventions` also FAILs on a missing key (spec 016 FR-014: persisted shape ⇒ doctor check) |
| `devclaw/telemetry.py` | Modified — `steering.human_steers` removed; usage note corrected; CLI render line |
| `tests/test_runner_acp.py` + `tests/acp_fake_agent.py` | Extended — the allowlist class test gains the identity case (`echo_git_identity`) |
| `tests/test_goal_state.py` | Extended — the ledger key: repeats are no-ops, pre-key commit rows are purged once at bootstrap (verbs kept, keyed ledger never purged again), verbs stay append-only |
| `tests/test_doctor.py` | Extended — the seeded-fault test parametrized over the missing table and the missing key |
| `docs/reference/env-vars.md`, `docs/INDEX.md` | The `DEVCLAW_GIT_EMAIL` row says where the identity now reaches |
| `specs/018-scorecard-ratchet/contracts/scorecard-output.md` | `human_steers` marked removed |

## Requirements

1. A commit made by the agent inside the sandbox is authored as
   `git_identity.py`'s identity, with the model's `Co-Authored-By` trailer
   untouched.
2. The same (goal, sha) recorded N times is one intervention row.
3. `steer`/`resume`/`decide`/`correct_implementation` rows are never
   collapsed.
4. Exactly one field on the scorecard counts the owner's steers.
5. Commit rows recorded before the key are deleted at the first restart
   (ruled by Denys 2026-09-08): none was recorded against a real identity,
   so none is evidence. Verb rows are untouched.

## Plan

Forward the identity; key the ledger; drop the second count; fix the note.

## Tasks

- [x] `GIT_*` passthrough in the runner allowlist + fake-agent echo test
- [x] Purge-then-index bootstrap (once, gated on the key) + `INSERT OR IGNORE`
- [x] Doctor check on the key + seeded-fault parametrization
- [x] `human_steers` removed, usage note corrected, spec 018 contract annotated
- [x] env-vars row + INDEX tag
- [x] Full suite + `ruff check .` + `mypy` + `lint-imports` green

## Done when

- The next worker commit on the VPS carries `devclaw@local` (or the
  configured `DEVCLAW_GIT_EMAIL`) as author.
- `non_worker_commits` over a week equals the count of distinct commits on
  goal branches by anyone other than that identity.
- `get_scorecard_metrics` carries one steer count.
