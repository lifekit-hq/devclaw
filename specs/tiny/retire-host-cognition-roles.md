# TinySpec: retire four host cognition roles (spec 037 loop-shape demolition, cut 2)

**Spec**: `specs/037-loop-shape-demolition/spec.md` (US2 — ruled 2026-09-06, executed via the tinyspec lane)
**Branch**: demolish/host-cognition-roles
**Date**: 2026-09-06
**Status**: done
**Complexity**: small (mechanical removal)

## What

Four `claude --print` roles with no measured consumer are deleted outright —
prompts, parsers, callers, config flags, MCP tools, tests and docs:

| role | what it did | why it goes |
|---|---|---|
| plain-language summarizer (`goal/summary.py`, `prompts/owner-summary.md`) | rewrote every OWNER ping before delivery | 12 failure rows, a second wording of every ping, the owner reads logs |
| self-triage interceptor (`goal/triage.py`, `prompts/self-triage.md`) | enriched the DB-size alarm with a proposed fix | one eligible ping kind, never acted on; the problems catalog + Issues carry triage |
| trend detector (`trend_detector.py`, `trend_signals.py`, `bookmark.py`) | cross-session pattern notes into the vault | 21 failure rows, zero decisions changed (the digest skill's own retirement test) |
| on-demand direction eval (`GoalService.evaluate_goal`, the `evaluate_goal` tool) | an evaluator call outside a done proposal | every nightly call on a pointer goal wrote "done_when is literally unspecified" (#795 class) |

After this PR the evaluator has exactly one caller: the done-gate on a done
proposal. Owner pings go out raw. The `review_trends` tool is gone with its
data source.

## Relocation, not deletion

- Triage → the problems catalog (`list_problems`) and GitHub Issues, which
  already carry the canonical intent.
- Summaries → the increment's own summary in the PR body and the goal log.
- Trends → the eng-health ratchet and the scorecard (mechanical, on demand).
- Direction reads → `get_goal` / `tail_goal` (zero cognition) and the
  done-gate's verdict.

## Context

| File | Change |
|------|--------|
| `devclaw/{trend_detector,trend_signals,bookmark}.py`, `devclaw/goal/{summary,triage}.py`, `devclaw/prompts/{owner-summary,self-triage}.md` | deleted |
| `devclaw/config.py`, `devclaw/model_tiers.py` | `DEVCLAW_GOAL_PLAIN_SUMMARY`, the `DEVCLAW_TREND_*` block and the `summary`/`triage`/`trend` tiers removed |
| `devclaw/goal/service.py` | `_summary`/`_triage`/`_trend_detector`/`read_trends`/`evaluate_goal` and their wiring removed |
| `devclaw/goal/tick*.py` | `summarize=`/`summary_caller`/`trend_detector`/`triage_caller` threading removed; `_notify` sends as written; the DB-size alarm is a raw ping |
| `devclaw/state_store/control.py` | trend cooldown/fingerprint meta helpers removed |
| `devclaw/server/tools/{goals,observability,__init__}.py`, `prompt_anatomy.py` | `evaluate_goal` and `review_trends` tools gone |
| `tests/test_goal_tick.py`, `tests/test_goal_tick_lock.py` | the summarizer and trend fakes and their tests removed (symmetric ratchet); no new tests |
| `CLAUDE.md`, `README.md`, `.claude/rules/cognition-prompts.md`, `docs/architecture.md`, `docs/reference/env-vars.md`, `docs/INDEX.md`, `docs/runbooks/vps-waiter-deploy.md`, `.claude/skills/devclaw-status/SKILL.md`, `evals/*` | roles removed from prose; INDEX tags |

## Outside this repo (Denys)

- `deploy/docker-compose.devclaw.yml` still defines the `ops-agent` service,
  whose only method is `evaluate_goal` (lifekit-stack `ops-agent/README.md`:
  "one method: evaluate_goal"). It now calls a tool that does not exist —
  loud, zero cognition. Retire the service on the box / in lifekit-stack.
- The vault file `~/memory/projects/devclaw/trends.md` on the box stops
  growing; delete or keep as history.

## Done When

- [x] The four roles and their surfaces are gone; `grep` for the names finds only history in `docs/audits/` and `evals/runs/`
- [x] `tests/test_env_vars_doc_sync.py` and `tests/test_harness_docs_map.py` green (docs and config in step)
- [x] Full suite green, ruff + mypy clean
- [x] Zero-token idle and fail-closed tripwires untouched
