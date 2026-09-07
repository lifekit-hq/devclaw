# docs/ — index & currency map

Every doc under `docs/`, its one-line purpose, and a **currency tag** so a new
reader knows what to trust. Tags:

- **CURRENT** — verified against code; load-bearing claims hold.
- **DECISION RECORD** — a frozen ADR in `decisions/`: the decision stands, the
  point-in-time system descriptions are not maintained for drift.
- **STALE — see note** — contains at least one claim the code contradicts; note says what.
- **FROZEN / HISTORICAL** — a record of a completed cutover or a retired scheme; kept for the paper trail, never audited for drift.
- **SNAPSHOT** — a dated point-in-time record under `audits/`; never updated for drift, superseded by the next snapshot.

Currency is verified by grepping each doc's load-bearing claims against the code, not
by trusting the doc's own "Status:" line. When you change behavior that a doc
describes, fix the doc **and** update its tag here in the same PR.

## Layout

```
docs/
├── architecture.md    the system doc — mental model + the locked contract
├── flows/             temporal traces (one task; dispatch → PR)
├── reference/         look-up tables (env vars)
├── runbooks/          operational procedures (shakedown, VPS deploy)
├── decisions/         FROZEN 2026-08-13 — historical ADRs (pipeline retired → speckit)
└── proposals/         FROZEN 2026-08-13 — historical direction drafts (pipeline retired → speckit)
```

Since 2026-08-13, behavior-changing work starts in `.specify/` (the speckit
pipeline — see `.claude/rules/speckit-workflow.md`); specs under
`specs/` are the living direction+execution artifacts. The pre-speckit
proposals and ADRs were removed from the tree on 2026-09-06 — see History below.

## System

| Doc | Purpose | Currency |
|---|---|---|
| [`architecture.md`](./architecture.md) | **Start here.** Part I: the one-sitting mental model (five layers, two chains, the heartbeat, one task's journey, where state lives). Part II: the **locked contract** — per-layer contracts, the invariants (incl. grounded cognition, auto-heal), testability/replaceability, the question tree, the code map. | **CURRENT** — verified 2026-09-06: Part I/II claims checked against the code; the gate section rewritten to match `quality/gate_policy.py` (`ALWAYS_HARD` verify/materialize/change_class/test_integrity/delivery_trust/done_gate, `DIAL_ABLE` browser/review/slice) and the `gate_pipeline` order, with the retired declared-scope gate stated once; dead references (elicitation.py, goal_docs, view_migration.py, DEVCLAW_SELF_TRIAGE, the claude_sdk engine, phases/registry, 12 pruned tests) removed; ADR 0001's agent-vs-box orthogonality folded into the layer-4 section; change history in git (`git log -- docs/architecture.md`) *Updated 2026-09-06*: one task's journey step 2 — the tick's prep is admission only; the branch rides on the action and the queue places it at run start, before the baseline capture (shared-workspace class); the admission lint's undecided-choice judge fails closed (tinyspec `eng-health-fix-now`). *Updated 2026-09-06*: the box's kernel-side fence named in the layer-4 section (tinyspec `sandbox-kernel-fence`). *Updated 2026-09-06*: the five-layers section names the import-linter contracts that enforce the layer order (tinyspec `import-contracts`). *Updated 2026-09-06 (spec 037)*: layer-3 row and the self-triage paragraph rewritten for the four retired cognition roles. *Updated 2026-09-06 (#847)*: the admission lint refuses a change in another repository and lints a pointer goal's referenced contract (tinyspec `admission-lint-pointer-goals`). *Updated 2026-09-07 (spec 038)*: loop-health paragraph — idle attribution by cause, the not-stuck rate, the self-heal rate, the `health` StateStore mixin. |

## Flows

| Doc | Purpose | Currency |
|---|---|---|
| [`flows/task-execution.md`](./flows/task-execution.md) | Temporal trace of ONE task, every hop (node 1 waiter → node 2 devclaw-mcp → node 3 ephemeral sandbox), with a "fails if" rail. | **CURRENT** — verified 2026-09-06 against `engine/sandcastle.py`, `runner/runner.py`, `engine/runner_io.py`, `task_change.py`, `quality/gate_pipeline.py`, `queue/settle.py`, `goal/remote_checks.py`, `goal/delivery_strategy.py` (gate-pipeline step, branch name, goals-dir path, env-deficiency kind fixed; the 2026-06 cascade block marked historical); change history in git (`git log -- docs/flows/task-execution.md`) *Updated 2026-09-06*: node 2's `docker run` carries the kernel-side fence (`--pids-limit 4096 --cap-drop ALL --security-opt no-new-privileges`, tinyspec `sandbox-kernel-fence`). |
| [`flows/delivery.md`](./flows/delivery.md) | How dispatches become PRs: the one delivery shape (a shared goal branch, one cumulative PR a human merges), the dispatch cap. | **CURRENT** — rewritten 2026-09-06 to the spec 025 doctrine: nothing merges mid-flight (#486), the confirmed-achieved close squash-merges the cumulative PR (`goal/merge_on_close.py`, same-green-head CI check `_ci_hold_before_merge`, one bounded conflict self-heal, `mechanical:merge_failed` park + lane release); the #641 section is a three-line history note; change history in git (`git log -- docs/flows/delivery.md`) |
| [`flows/autonomous-issue-pipeline.md`](./flows/autonomous-issue-pipeline.md) | End-to-end target-state flow: intake → readiness grade → dispatch → speckit scope → build → merge, with the label state machine and the human control points. | **CURRENT** — rewritten 2026-09-06: stage tags match the code (stage 1 grading exists; spec 007 PARKED; self-fix pickup gated by the human `accepted` label, no env flag; the real gate chain; merge-on-close at stage 6; spec 008 US3 label-routing NOT BUILT; live label set from `intake.py` + `self_issue.py`; spec 023 webhooks ACTIVE); change history in git (`git log -- docs/flows/autonomous-issue-pipeline.md`) *Updated 2026-09-06*: goal-path rows carry `target_branch` (= `goal/<id>`, done-check included); placement at run start, baseline re-captured per run, reused only on pause-resume. |

## Reference

| Doc | Purpose | Currency |
|---|---|---|
| [`reference/devclaw-manifest.md`](./reference/devclaw-manifest.md) | The per-project `devclaw.json` manifest: fields, precedence (most-specific-wins, resolved live), the merged-base trust boundary, error posture; machine schema beside it (`devclaw-manifest.schema.json`). | **CURRENT** — verified 2026-09-06 against `devclaw/project_manifest.py`, `devclaw/env_cap.py`, `devclaw/doctor/checks_project.py` (`validation` key, `ci:definition`, the two PR-only writers, pruned test reference fixed); change history in git (`git log -- docs/reference/devclaw-manifest.md`) |
| [`reference/env-vars.md`](./reference/env-vars.md) | Single source of truth for every env var the runtime reads, grouped by purpose. | **CURRENT** — verified 2026-09-06 against `devclaw/config.py` + `tests/test_env_vars_doc_sync.py` green (set parity both ways, every default matches); dead cognition roles in prose (planner, scope grill, firming/decomposer, failure-analysis judge, `goal_docs`/`programs`) replaced with the live roles from `devclaw/model_tiers.py`; change history in git (`git log -- docs/reference/env-vars.md`) *Updated 2026-09-06 (spec 037)*: `DEVCLAW_GOAL_PLAIN_SUMMARY` and the trend-detection section removed with their code. *Updated 2026-09-06*: spec 036 US1 — `DEVCLAW_COGNITION_RETRIES` now covers the SERVER_ERROR class too (a provider outage retries in-process before it reaches the fleet-wide pause). |
| [`reference/intake-shape.md`](./reference/intake-shape.md) | The intake shape — how any ask (human or agent, any channel) enters devclaw via the `file_intake` MCP tool: fields, synchronous rejection rules, server-stamped provenance, the issue-URL receipt and its open/closed lifecycle, and the stage-1/stage-2 split (filing is unprivileged; dispatch stays with the authorized dispatcher). Enforcement is `devclaw/intake.py`; this page is the canonical narrative. | **CURRENT** — verified 2026-09-06 against `devclaw/intake.py`, `devclaw/intake_readiness.py`, `devclaw/server/tools/intake.py`, `devclaw/goal/issue_ref.py` (async Graded step, third staleness axis, fail-closed missing `## Done when`, issue-as-contract stage 2 added); change history in git (`git log -- docs/reference/intake-shape.md`) |

## Runbooks

| Doc | Purpose | Currency |
|---|---|---|
| [`runbooks/doctor.md`](./runbooks/doctor.md) | The post-redeploy checklist as one read-only, zero-LLM verb: the `doctor` MCP tool / `devclaw doctor` CLI — instance invariants (migration markers, legacy shapes, OAuth expiry, skills bundle, run-window raw key) + per-project link/workspace checks, every finding naming its remedy verb. | **CURRENT** — verified 2026-09-07 against `devclaw/doctor/__init__.py`, `checks_instance.py`, `checks_project.py`, `devclaw/cli.py` (marker-module claim replaced; later check families listed; loop-health tables check added by spec 038); change history in git (`git log -- docs/runbooks/doctor.md`) |
| [`runbooks/webhooks.md`](./runbooks/webhooks.md) | Enabling event-driven triggers (spec 023): the `DEVCLAW_WEBHOOK_SECRET` knob, path-scoped Tailscale Funnel exposure, per-repo `gh api` webhook wiring, verification and failure posture. | **CURRENT** — verified 2026-09-06 against `devclaw/server/routes/webhooks.py`, `devclaw/config.py`, `deploy/docker-compose.devclaw.yml`, `goal/events.py`; change history in git (`git log -- docs/runbooks/webhooks.md`) |
| [`runbooks/live-shakedown.md`](./runbooks/live-shakedown.md) | Exercising the real pipeline (logged-in `claude` + docker) layer by layer, L1 single task → L5 abort. | **CURRENT** — verified 2026-09-06 against `devclaw/server/tools/tasks.py` + `goals.py` (L1/L2 examples rewritten to the live `dispatch_task` / `create_goal(issues=[…])` signatures — the old ones named a tool and a parameter that no longer exist), `.github/workflows/ci.yml` (billing-locked claim removed), `engine/sandcastle.py`; change history in git (`git log -- docs/runbooks/live-shakedown.md`) |
| [`runbooks/vps-waiter-deploy.md`](./runbooks/vps-waiter-deploy.md) | Deploying the OpenClaw waiter + devclaw to the VPS; the waiter's tool menu. | **CURRENT** — verified 2026-09-06 against `devclaw/server/tools/*` (menu gains `validate_product`, the spec 031 Problem verbs, doctor/intake/schedule/hold verbs); the 2026-06-24 box snapshot marked historical; change history in git (`git log -- docs/runbooks/vps-waiter-deploy.md`) |
| [`runbooks/devclaw-self-deploy.md`](./runbooks/devclaw-self-deploy.md) | The self-deploy cutover (spec 005): from-source `deploy/Dockerfile` → ghcr push → own `devclaw` compose project; the goal-safe volume-adoption order, the lifekit-stack companion change, rollback, and cold first-deploy. | **CURRENT** — verified 2026-09-06 against `deploy/Dockerfile`, `deploy/deploy-devclaw.sh`, `deploy/deploy-devclaw-auto.sh`, `.github/workflows/deploy.yml`, `devclaw/boot_guard.py` (auto-deploy lane added; completed cutover steps marked); change history in git (`git log -- docs/runbooks/devclaw-self-deploy.md`) *Updated 2026-09-06*: the ops-agent stanza and its incident dir are gone (tinyspec `deadman-metrics`); the watcher is Prometheus + Grafana in lifekit-stack. |

## Audits (SNAPSHOT — dated, never updated for drift)

Point-in-time reads of the loop and the codebase, kept as self-contained HTML
so the visual form (score tiles, night strips, tables) survives. Each is
superseded by the next; git history is the series.

| Doc | Purpose | Currency |
|---|---|---|
| [`audits/2026-09-05-scorecard.html`](./audits/2026-09-05-scorecard.html) | The production-readiness ratchet read on 2026-09-05: decided-merge 1.00 PASS, first-pass 0.36 FAIL, wedge-free 2/5 FAIL; fourteen-night strip, done-gate grade distribution, smells by discipline, next moves (#793, #817, calibration evals, spec 034). | **SNAPSHOT 2026-09-05** — computed from `get_scorecard_metrics` + `evals/cycles.json` at instance `cec3aa8`. |
| [`audits/2026-09-06-engineering-audit.html`](./audits/2026-09-06-engineering-audit.html) | Engineering-practice audit of the tree at `8499fb6`: separation of concerns 6, lean/noise 6, prompt engineering 6, harness 7, context management 6; findings with file:line evidence, brake inventory, and the proposed recurring `eng-health` ratchet skill. | **SNAPSHOT 2026-09-06** — four independent read-throughs, highest-stakes findings re-verified by hand; the first baseline for the proposed eng-health ratchet. |
| [`audits/eng-health.md`](./audits/eng-health.md) | **Keep-latest** engineering-health report: the mechanical metrics from `evals/measure_eng_health.py`, the delta against the previous run, and every finding's disposition (fix now / promote to guard / spec / accept-with-regrade-date). Overwritten by each `/eng-health` run; git is the series. Sidecar `audits/eng-health.json` is the machine baseline the next run diffs against. | **CURRENT** — seeded 2026-09-06 from the engineering audit; regenerated by the skill, never hand-edited. |

## History (in git since 2026-09-06)

The proposal→ADR pipeline was retired 2026-08-13 (full speckit). Its 32
artifacts — ADRs 0001–0003 and 0005–0012, every proposal and the archived
drafts — were removed from the tree on 2026-09-06: nothing load-bearing
referenced them (the surviving decisions are restated in `CLAUDE.md`,
`architecture.md`, `flows/task-execution.md` and the specs that superseded
them). Read any of them with `git log -- docs/decisions docs/proposals` or
`git show 9e277a2:docs/decisions/<file>`. `ROADMAP.md` (the 2026-07
version-ladder, tracking a milestone closed 2026-07-22) and the completed
`runbooks/project-reference-key-cutover.md` went the same way.

| Doc | Purpose | Currency |
|---|---|---|
| [`decisions/0004-eval-workbench.md`](./decisions/0004-eval-workbench.md) | Eval-driven guardrail proportioning + the living eval workbench: stop hardening blind — measure with the existing instrument (`measure_passrate` + judge), rebalance Python guardrails toward context/prompt engineering, calibrate every shed to the WEAKEST target model (the 2×2; never delete on the Claude column alone), surface run history as a console "Evals" tab over an `eval_runs` table. Phased: Claude baseline → workbench MVP → judge-as-scorer → shed-candidate ID → local-model calibration + shedding LAST. | **DECISION RECORD** — accepted 2026-07-20 (graduated from `proposals/eval-workbench.md` under the spec lifecycle). Implementation phased; step-1 execution started 2026-07-20. **Step 2 amended 2026-07-21 by [ADR 0006](./decisions/0006-continuous-eval-projection.md)** (two-source outcome projection replaces the basket-only `eval_runs`). |

## Where the docs are NOT

- **The agent harness contract** — [`../CLAUDE.md`](../CLAUDE.md): the distilled
  working contract an agent reads before touching the repo. It deliberately
  duplicates the invariants in condensed form; `architecture.md` is the canonical
  statement.
- **The product narrative** — [`../README.md`](../README.md).
- **Worker-layer skills** — `.agent/skills/` (product, not harness docs).
- **Generated views** (`STATUS.md`/`log.md`/`deliveries.md` under goal dirs) —
  projections, never hand-edited, never audited here.
