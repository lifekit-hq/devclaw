# Feature Specification: devclaw v2 — thin Python, model reasoning

**Feature Branch**: `046-devclaw-v2`

**Created**: 2026-09-13

**Status**: DIRECTION RULED 2026-09-10/13 (Denys). Eight pillars confirmed. This page is
the whole spec: Denys reads it before any Python exists. No plan.md/tasks.md — the build is
ONE big-bang PR whose review is this page.

**Input**: Denys, 2026-09-10: "It should be simple, but it should be sharp. Clear separation
between programmatic things which are determined, and things that can be reasoned by a
model. Some Python and some reasoning. That's it." And 2026-09-13: "we rely too much on
Python and too little on model reasoning — it should be a thin layer of software and the
heavyweight should be model reasoning."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **ran but needed the owner** first, **stopped when it shouldn't**
  second. Both are produced by the same thing: a host that classifies a failed night into a
  typed hold and waits, instead of handing the night back to an engineer.
- **Number that shows it**: owner verbs per achieved goal **3.24** and clean cycles **4 of
  11** (`get_scorecard_metrics`, 2026-09-07); fs-431: **13 blocks, 5 owner verbs in 8 days**,
  closed by hand 2026-09-10. Fix commits per week **2 → 16 → 16 → 23 → 27** (git, weeks
  ending 2026-08-14 → 2026-09-11) while the goal layer grew **350 → 17,022 lines**; the
  2026-08-18 amputation (−2.4k) regrew +5.6k in three weeks. A converging system shows the
  opposite curve.
- **Cut when**: after 14 nights on v2, owner verbs per achieved goal or wedges are not
  below v1's last read → the VPS rolls back to tag `v1.2.0` and this spec is marked FAILED
  with the reason. A second cut: the v2 goal layer exceeds its size guard → the PR that
  grew it is the defect, not the guard.

## The root, named once

The host grew a **second brain**. August cut the eight LLM brains; a **mechanical** one
regrew in their place — twelve `blocked_kind`s, heal budgets, backoffs, holds, churn and
progress counters, conflict routing, typed Problems — because every failed night became a
host type (the line moved 2026-07-13, harden-loop #228–#238). Each type is a **copy of a
fact GitHub, CI or the worker already holds**, and every copy drifts (spec 045 fixed one
drift at four call sites). v2 has one actor that reasons — the session — and a host that
reads the world fresh and holds nothing it concluded.

## The pillars (confirmed 2026-09-13)

1. **Two actors, one line.** Python decides what is determined; the model reasons everything
   else. The line is written down in one place (this page + the session prompt); the layer
   that holds it fits one afternoon of Denys's reading. A size guard fails the build when it
   stops fitting.
2. **Software owns five things.** Safety (sandbox, credential strip). Money (quota pause and
   resume; a per-goal daily session cap read from task rows). State (single writer, CAS).
   Verdict (CI green on the delivered head + one grounded done-gate). Protocol (issue in,
   PR out; `decide`, `cancel`). Python outside these five needs a written reason.
3. **The issue is the contract.** One schema, end to end, done-gate included. The host never
   triages, grades or rewrites it.
4. **The session is the engineer.** One generic prompt. Speckit inside the sandbox. Recovery
   = read the repo, the PR and the CI, and continue, the way a developer does on Monday. The
   only exits are a delivery, a done proposal, or `BLOCKED` with one question.
5. **The host holds no derived state.** It stores decisions, the quota pause, and the
   **last-seen world fingerprint per goal** — nothing else. A fingerprint is an observation
   (losing it costs one session), never a judgment (losing a hold cost fs-431 days). No
   failure kinds, no budgets, no holds, no counters. The day the fingerprint needs a field
   that encodes meaning ("conflict seen twice"), it is a hold again.
6. **Zero tokens when nothing changed.** Reading the world is free. A session is spawned
   only when the fingerprint moved.
7. **Done is never the agent's word.** The one place fail-closed survives.
8. **The owner acts only on decisions.** A failed night becomes a fact handed to the session
   or a line in its prompt — never a host mechanism, never an owner verb.

## 1. The tick rule (the whole control plane)

Every ~15 min, for every goal that is not closed or cancelled:

```
if account quota is paused           → nothing            (money)
if a session is running for the goal → nothing
if sessions today ≥ cap (task rows)  → nothing, log once  (money; the ONE brake)
world = fingerprint(
    PR: exists?, head sha, state (open|merged|conflicting),
    CI rollup for that head (green|red|pending|none),
    last comment id on the issue and on the PR,        ← decisions + gate verdicts live HERE
    last session's exit line)
if world == goal.last_seen           → nothing            (pillar 6)
if PR merged                         → close the goal      (protocol)
if last exit == DONE and CI green    → done-gate: evaluator over the repo vs the contract
                                       achieved → squash-merge (spec 025 stands) → close
                                       not achieved → post the findings as a PR comment
                                       (the world changed; the next tick spawns)
else                                 → spawn ONE session with the world as facts
                                       goal.last_seen = world  (written at spawn)
```

That is the entire state machine. **GitHub is the state**: gate verdicts are PR comments,
`BLOCKED` questions are issue comments, `decide` writes an issue comment. The host never
routes on *why* the world changed — the session reads why.

What v1 typed, v2 reasons: a red CI, a conflicting PR, a dependabot bump in the range, a
partial implementation, a lost branch, a repeated failure. A `BLOCKED` with no answer yet
is an unchanged fingerprint: zero tokens for as long as it takes, and the owner's answer is
the one thing that wakes it.

**Measured at this seam**: a session that starts and finds nothing to do (exit line
`NOTHING`) is a fingerprint defect. It is the seam's only number.

## 2. The session prompt (one markdown file, the only instruction the host gives)

```
You are the engineer on this goal. Contract: issue #{issue} (full text below). Repo: ./
on branch {branch}. PR: {pr_state} (#{pr} at {sha}; CI {ci}; failing checks: {checks}).
Since the last session: {new comments — owner decisions, gate verdicts}.
Last session ended: {exit line}.

Do the next thing a developer would: if the PR conflicts or CI is red, fix that first;
if no plan exists in the repo, run speckit (specify → clarify with defaults → plan →
tasks) into the repo; otherwise take the next unfinished task. Read .devclaw/ first and
leave your notes there. Verify locally before pushing. Do not merge. Do not edit CI or
gate inputs. Do not ask for anything you can find in the repo. If you are repeating an
attempt a previous session already made and it failed the same way, stop and BLOCK.

End with exactly one line:
  DELIVERED: <what landed on the branch>
  DONE: <why the contract is met>          (a proposal; the done-gate decides)
  BLOCKED: <the one question the owner must answer>
  NOTHING: <why there was nothing to do>   (this is a devclaw defect; say why)
```

## 3. The delete list — and what earns its way back

**Start from an empty package.** Nothing from v1 is kept by default; a file is copied in
only when a pillar names its job **and Denys has read it**.

| v1 (lines) | v2 | Pillar |
|---|---|---|
| `devclaw/goal/` 17,022 — tick, guards, settle, done-gate routing, problems, decisions, pins, churn, holds, admission lint, intake grading | **rewritten**, target ≤ 1,500 (size guard) — create/cancel/decide, the tick rule, done-gate call, fingerprint | 1, 5 |
| `devclaw/queue/` settle routing 2,045 | execute + settle to a task row; no routing | 2 (state) |
| `devclaw/server/` 5,056 · 48 tools | ~12 tools: `create_goal` `cancel_goal` `decide` `get_goal` `list_goals` `get_status` `get_events` `register_project` `list_projects` `doctor` `set_run_schedule` `clear_usage_pause`. Gone: steer/resume/correct/strictness/verify_cmd/intake grading/scorecard/loop_health/problems/holds | 2 (protocol), 8 |
| tables: 27 (`goal_*` ×12, problems, pins, spans, ledgers) | `goals`, `decisions`, `tasks`, `events`, `usage_pause` | 5 |
| `devclaw/doctor/` 2,133 | only checks on state that still exists | 2 |
| `specs/` 41 numbered + 74 tiny | leave the tree (git keeps them); this page + the constitution rewritten | "in the tree = alive" (#905) |
| `tests/` 99 files, 29,626 lines | tripwires for the five domains only; the ratchet is symmetric | testing rule |

**Expected survivors** (whitelist candidates, byte-for-byte unless Denys strikes them):
`engine/` (sandcastle, host, workspace) · `runner/` (ACP runner, skills) · `credentials.py`
· `task_change.py` (the span) · `state_store/` (event log, CAS) · `delivery/` · `llm_call.py`
+ `cognition.py` (the OAuth strip) · `prompts/goal-evaluator.md` · `quality/` only the
always-hard `change_class` · `loom/limits` (quota pause) · `config.py` · `server/http`,
`lifecycle`, auth · self-deploy.

## 4. The cutover

1. Merge release PR #862 → **`v1.2.0` is the freeze**: the VPS rollback point and the
   historical record. Nothing after it touches the old goal layer, not even a fix; a night
   that fails before cutover waits.
2. **One branch, one PR**, `BREAKING CHANGE:` footer → release-please cuts **2.0.0**.
   `CLAUDE.md`, `AGENTS.md`, `ARCHITECTURE.md`, the constitution and `docs/` rewritten in
   the same PR to the new shape.
3. **Live**: one self-deploy of 2.0.0; open goals recreated as pointers against the new
   tables (the "goals are durable, cancel + recreate" doctrine). The v1 DB stays on disk
   under the tag for 7 days, then goes.
4. **After** (the discipline that makes it stick): a failed night becomes a fact in the
   prompt or a line in the skill — never a host type. Two guards fail the build: the goal
   layer's size, and any new column that names a failure kind, budget or counter.

## Rejected alternatives (direction memory)

- **New repo / from-scratch v2 elsewhere** — throws away 15k hardened lines (sandbox fence,
  OAuth strip, CAS, span, runner, delivery), CI, release, image build, self-deploy, and the
  history that is the CV; the summer v2 attempt was abandoned exactly this way.
- **Seam-by-seam demolition** — weeks of tokens on a 1,800-line tick, and August proved it
  regrows faster than it is cut.
- **Adopt or fork OpenHands** (2026-09-10 code-level teardown) — their automation has the
  shape and lacks the verdict (done = the agent's transcript) and pause-and-resume (quota
  disables the job); a solo fork of 144k lines at 100 commits/month is the demolition tax
  on someone else's code. Borrow: the five stuck patterns as an in-session brake, ACP
  token-usage extraction, subject-keyed threads. Keep the ACP seam as is — it is where their
  SDK could become the worker later. Re-read 2026-12-10.
- **Typed holds with better heuristics** — every hold is a copy of a fact the world already
  holds; better copies still drift. The fingerprint stores what was *seen*, never what was
  *concluded* — that is the whole difference.
