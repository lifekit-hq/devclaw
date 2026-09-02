# TinySpec: profile the burn, then filter the output

**Branch**: feat/burn-profile-and-output-discipline
**Date**: 2026-09-02
**Status**: done
**Complexity**: small

## What

Two things, in the order they must happen: a mechanical way to see where a
worker's context goes, and the first fix that measurement justified.

**The measurement.** Every explanation offered for the worker hitting the
context wall — planning ceremony, whole-file re-reads — was a hunch. The
events table already records every tool call and every `usage_update`; nothing
read them back. `evals/burn_profile.py` walks a task's stream in order and
charges each rise in the agent's own cumulative usage to the tool call that
preceded it. Per-task and aggregate modes. Zero LLM, read-only.

**What 25 tasks said** (2026-09-02, ~3.1M tokens of growth attributed):

| finding | evidence |
|---|---|
| Read 38.7% · Bash 36.1% · Edit 10% · Write 6% | by-tool split |
| **zero** redundant whole-file reads | the re-read hunch was wrong |
| 57 separate greps of three files, ~42k tokens | `config.py` ×20, `tick.py` ×23, `service.py` ×14 |
| grep verbs ≈ 9.8% of all growth | `grep -n` alone 208k |
| one `pytest` run ≈ **25k tokens** | vs ~400 for a filtered `dotnet build` — same job, 50× the cost |
| per-task growth 120k–264k | the 200k window was structurally too small |

**The fix.** No worker skill said anything about output cost. `_common.md`
gains one imperative rule: every tool result is permanent context — filter
test/build runs to the failures, cap searches, ask a file one broad question
instead of ten narrow ones, read ranges of large files. One home, every kind.

## Context

| File | Role |
|------|------|
| `evals/burn_profile.py` | New — the analyzer, both modes |
| `evals/README.md` | Will be modified — document it beside the other harnesses |
| `runner/skills/_common.md` | Will be modified — the output-discipline rule (always prepended, every kind) |
| `devclaw/state_store/` events table | Context — the data source; unchanged |

## Requirements

1. The analyzer is pure mechanism over the events table: no LLM, read-only,
   runnable against any `devclaw.db` (`$DEVCLAW_DB`, default `./devclaw.db`).
2. Tool calls are resolved by merging every event that carries a
   `toolCallId` — a call arrives as several events and only some carry
   `rawInput`.
3. Attribution charges each `usage_update` rise to the most recent tool
   call; a task with no usage stream contributes nothing rather than noise.
4. The skill rule states each thing ONCE, imperatively, with no incident
   history — the war story lives here and in the commit, not in the prompt
   (cognition-prompts rule).
5. Skills stay plain markdown (no frontmatter, no native tool wiring).

## Plan

1. Land the analyzer with a docstring that records what it overturned, so the
   next person optimising from a hunch reads the number first.
2. Document in `evals/README.md`.
3. Add the rule to `_common.md` above the per-repo skills section.

## Tasks

- [x] `evals/burn_profile.py` — per-task + `--aggregate N`; validated against
      the live instance (25 tasks) and smoke-tested on the local db
- [x] `evals/README.md` section
- [x] `_common.md` output-discipline rule
- [x] `ruff check .` clean; full suite green (the skill-content guards)

## Done When

- [x] `python evals/burn_profile.py --aggregate 25` reproduces the table above
      against the live DB
- [x] Every worker kind is told, once, that tool output is permanent context
- [ ] Re-profile after a week on Opus: `pytest` per-run cost and grep share
      both down — the number, not the feeling, decides whether the rule worked
