# Implementation Plan: After a stop

**Branch**: `feat/after-a-stop` (US1) · `feat/after-a-stop-us2` stacked (US2) | **Date**: 2026-09-08 | **Spec**: [spec.md](./spec.md)

## Summary

Layer 2 only. US1: a Decision is work (`tick.py` should_plan), the closed-issue shortcut yields to pending work, an owner `accept_close` closes through a new zero-cognition `_finalize_accepted_close` in `tick_donegate.py` (rollup → merge-on-close → ACHIEVE), `cancel` executes in `service.resolve_problem` / `_apply_problem_default`. US2: the cap raises a Problem (`problems.CONTINUE`), fetch failures block `mechanical:prep`.

## Constitution Check

- III zero-token: every new branch is store reads + one bounded `gh` read; `FakeClaude.calls == 0` pinned. IV single writer: Decision + transition in one transaction (existing shape). V: amended — the owner's explicit accept closes on mechanical facts (CI green, merged); the evaluator stays the only machine ACHIEVE emitter. VI loud: the cap's Problem states the honest reason; a fetch hold logs each recheck. VII class: six mechanisms at one seam → one policy (a stop is a heal, a Problem with a default, or a Decision the next tick executes). IX: brake inside the **protocol** (what a verb produces) and **state** domains.

## Project Structure

```
devclaw/goal/decisions.py        pending_since(rows, last_plan_at) · accepted_close(rows) · CONTINUE label
devclaw/goal/problems.py         CONTINUE option; ACCEPT_CLOSE consequence text
devclaw/goal/tick.py             should_plan includes pending Decisions; closed-issue shortcut yields; accept-close branch; fetch → mechanical:prep; default cancel
devclaw/goal/tick_donegate.py    _finalize_accepted_close (zero cognition); _live_contract fetch → mechanical:prep
devclaw/goal/tick_dispatch.py    cap → Problem (continue/cancel), second cap → no default
devclaw/goal/service.py          resolve_problem: cancel executes
.specify/memory/constitution.md  V amended (2.10.0)
tests/test_goal_tick.py · tests/test_merge_on_close.py · tests/test_done_when_scenarios.py   class tests extended
docs: CLAUDE.md, docs/architecture.md, docs/INDEX.md
```
