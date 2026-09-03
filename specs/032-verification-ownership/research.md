# Research — spec 032 Verification ownership

Phase 0 output. Every decision below was grounded by reading the seam it lands on
(file:line anchors are into this worktree at `main` = c77d8ce). No `NEEDS CLARIFICATION`
remains: the three spec questions were ruled 2026-09-03 (Q1 C, Q2 B, Q3 A).

## R1 — The CI fact already exists, in the wrong place, keyed to the wrong head

**Finding.** `devclaw/goal/remote_checks.py` already reads CI (`check_branch`, four `gh api`
calls, pure fold `combine_states` at `:110`, verdicts `passing|failing|pending|infra_broken|
none|no_workflows|unknown`). Its only consumer is `tick_donegate.py:433-493`: it runs
*after* `_evaluator.evaluate(...)` at `:420` has spent the LLM call, only when the verdict is
`achieved`, keys to the **branch head** (`gh api …/commits/{branch}`) rather than the PR
head, and converts `pending` into `off_track` + a `[remote-checks]` correction that
re-dispatches a worker (`:474-479`, `:722`). That is the literal fs-431 mechanism: the
verdict string "the branch's real CI contradicts the close: remote checks are failing" is
this code, not the model. Under `CI_GATE_MODE="flexible"` (`:66`, env `DEVCLAW_GOAL_CI_GATE`)
`infra_broken` and `none` close anyway; `unknown` fails open (`test_unknown_remote_state_fails_open_but_logs`).

**Decision.** US1 is a *relocation + hold*, not a new subsystem:
- New `check_pr(pr_url) -> RemoteChecksResult` in the same module: one `gh pr view <url>
  --json headRefOid,baseRefName,statusCheckRollup` plus one `gh api repos/{o}/{r}/branches/
  {base}/protection/required_status_checks/contexts` (404 ⇒ no protection ⇒ every check
  counts). `combine_states` gains the required-name filter and returns `head_sha`,
  `failing_names`, `pending_names`. `check_branch` and its four-call reader are deleted
  (symmetric ratchet: their tests are rewritten for the new position).
- The read moves to the **top of `_open_done_gate`** (`tick_donegate.py:728`), before
  `prepare_ws` (`:757`) and before the `review_repository` dispatch (`:770`) — so a red or
  pending rollup costs neither the review sandbox nor the evaluator call.
- Outcomes: `passing` ⇒ record `ci_green_head` and proceed; `failing` ⇒ machine steering
  `[remote-checks] <names> failing on <head>` (source `auto-ci`) + `RESUME_IDLE`, no
  done-gate round counted (no cognition was spent; the worker re-dispatch is the correct
  next action and is bounded by the existing dispatch cap); `pending` / `unknown` ⇒
  `Event.BLOCK` with `blocked_kind="mechanical:ci"` and `pending_done_proposal=True`;
  `no_workflows` / `none` ⇒ `mechanical:env` hold naming "no CI definition" (Q3: the
  project should not have been dispatched; admission gains the same check, R6);
  `infra_broken` ⇒ typed Problem (`kind="env"`, raised_by `done_gate`, options fix-CI /
  declare-in-scope / cancel, default hold) — the one place a human is asked, because the
  project's own CI definition is broken and no worker may edit it.
- Merge side: `_resolve_done_gate` re-reads `check_pr` immediately before `_attempt_merge`
  (`:508`) and `_finalize_pending_merge` before `:830`; merge requires `passing` AND
  `head_sha == ci_green_head`. A moved head re-holds (`mechanical:ci`) and the done gate
  re-opens on the new head — the review is stale for a new head anyway.
- The post-evaluator block (`:433-493`), `CI_GATE_MODE`, and `DEVCLAW_GOAL_CI_GATE` are
  removed. Every `_gh`/`_run_gh` helper in `remote_checks.py`, `mergeability.py`,
  `merge_on_close.py` gets `asyncio.wait_for(…, 20s)` — today none has a timeout and a
  hung `gh` would block the tick loop (the spec's "provider unreachable" edge).

**Alternatives.** (a) Read at settle right after delivery — rejected: CI has just started,
the answer is always `pending`; the fact is needed where a decision is made. (b) Keep the
post-evaluator position and only add a hold — rejected: it still spends the review sandbox
and the evaluator on every red round (the fs-431 cost). (c) `gh pr checks` — rejected: no
head sha in its output; `pr view --json headRefOid,statusCheckRollup` gives both in one call.

## R2 — The hold needs one persisted flag; the heal is zero-token and throttled

**Finding.** A done proposal is detected from the settled task's `finished_detail` header
(`tick.py:667-673`). After a hold there is no new settle, so nothing would re-detect the
proposal. `GoalStatus` already carries per-site brake columns (`envcap_redispatches`,
`pending_merge_pr`, `merge_heal_attempted`, `env_hold_notified` — `models.py:252-310`)
and the blocked branch of the tick (`tick.py:369-393`) dispatches heals by `blocked_kind`
with a budget + backoff (`heal_attempts`, `next_heal_at`).

**Decision.** Two `goal_status` columns: `pending_done_proposal INTEGER NOT NULL DEFAULT 0`
and `ci_green_head TEXT NOT NULL DEFAULT ''`. `_handle_long_lived_advance` checks
`pending_done_proposal` right after the pending-merge finalizer (`tick.py:686`) and before
the project hold, calling `_open_done_gate` directly. The heal `_autoheal_ci` sits in the
blocked branch next to `_autoheal_env_cap` (`:383`): re-reads `check_pr` at most once per
`next_heal_at` window (reuse the heal backoff; the spec's "bounded wait"), `passing` ⇒
`_heal_unblock` + `pending_done_proposal` stays set so the next tick re-opens the gate;
`failing` ⇒ correction steering + unblock; still pending/unknown ⇒ stay, zero cost. Both
columns clear on `ACHIEVE`, on `cancel`, and on a productive settle (`tick_settle.py:282`
reset block). Doctor: `_goal_status_column_finding` (`checks_instance.py:600`) for both
columns + seeded-fault tests (`tests/test_doctor.py:389` shape).

**Alternatives.** Keep the goal in `verifying` with no in-flight ref — rejected: the
POLLING_DONE_GATE phase requires a ref and would read as a lost ref (#185 class). A meta
key instead of columns — rejected: goal state is `GoalStatus`'s, CAS'd; meta is for
instance-wide probe rows.

## R3 — The typed environment outlet forks at three existing seams

**Finding.** The worker's `BLOCKED:` hand-back is parsed once (`runner/runner.py:878-899`),
emitted as `{"status":"blocked","reason":…}` (`:1821-1841`), fails closed without retry on
the host (`settle.py:1236-1243`, `:1360-1414`), and becomes a typed `needs_answer` Problem
at the goal layer (`tick_settle.py:352-384`). The `mechanical:env` hold, its one-ping
marker and its heal already exist for declared capabilities (`tick_guards.py:357`, `:408`;
`env_cap.py` rows under `meta` key `env_cap_probe:<cap>[@<project>]`).

**Decision.**
- Runner: `_classify_block(reason)` — `^(env|environment)\s*[—:–-]\s*(.+)$` (case-insensitive)
  ⇒ `block_kind="env"`, `block_item=<text>`; anything else ⇒ `block_kind="contract"`. The
  payload gains both fields; `_RETURN_CONTRACT` (`runner.py:191`) documents the two forms.
- Host settle: `block_kind == "env"` ⇒ `last_failure = "worker reported environment
  deficiency: <item>"` (new marker, exported like `WORKER_BLOCKED_MARKER`), no retry,
  `record_problem(category="block", kind="env_deficiency", message=item, recovered=False)`
  — the catalog fingerprint normalizes the message so one item is one row; `terminal_count
  > 0` makes `should_file` eligible on the existing cadence (`self_issue.py:93`).
- Goal layer: the marker forks `tick_settle.py:352` to `_block_on_env_deficiency`: writes a
  red worker-reported row `env_cap_probe:worker:<slug(item)>@<project>` whose value carries
  `evidence`, `remedy`, and `env_ref` (the sandbox image reference + `DEVCLAW_GIT_SHA` +
  the project manifest hash), then calls the existing `_block_on_env_cap` (same
  `mechanical:env` kind, same one-ping marker). `red_caps_for` consults `worker:*` rows for
  the project regardless of declaration, so every goal on that project holds at admission
  (`tick_dispatch.py:185-206`). The row is green when the instance's current `env_ref`
  differs from the recorded one — the environment that failed is not the environment we
  have — which is how the hold heals without any human verb; `refresh_needed` skips
  `worker:*` rows (no probe runner). No spec-031 Problem is raised for an env deficiency:
  the pipeline owns it; the owner is informed once by the existing ping.
- `DEVCLAW_SELF_REPO` is unset on the live instance, so the self-issue path is currently a
  no-op there (`self_issue.py:289`). The plan records it as an instance-config prerequisite
  (`lifekit-hq/devclaw`), not code.

**Alternatives.** A new `blocked_kind` (`mechanical:env_worker`) — rejected: the hold,
ping and heal are identical to declared-capability holds; one kind, one story (spec 030
US3). A Problem per deficiency — rejected by the spec (no human stage for pipeline-owned
work). Heal by TTL — rejected: a timer is not a fact; the env_ref comparison is.

## R4 — Change classification lives in `ChangeSet`; the gate is always-hard and no-retry

**Finding.** `ChangeSet` (`task_change.py:48-82`) has no per-path data; binary detection
lives in `settle.py:253-267` from diff text; the glob matcher `loom/diff_paths.path_in_scope`
(`:66`) and the pure advisory scanner `quality/change_advisories.py:74` are the precedents;
the gate chain is built at `settle.py:1289-1302` (and the salvage twin `:1160-1171`),
`ALWAYS_HARD` at `gate_policy.py:39`. The dispatch brief handed to delivery IS the issue
text (`issue_ref.render_issue_context` → `Action.goal` → `deliver_change(goal=…)`), so an
in-scope declaration in the issue body is already available with no plumbing.

**Decision.**
- `ChangeSet.paths: tuple[ChangedPath, …]` with `ChangedPath(path, status, binary, cls)`,
  computed in `_capture_change` from `git diff --numstat -M base..head` (binary ⇒ `-\t-`).
  `classify_path(path, *, diff_hunk, in_scope)` is a pure function in `task_change.py` — the
  one home — with two glob tables: `GATE_INPUT_GLOBS` (`AGENTS.md`, `.github/workflows/**`,
  `.github/actions/**`, `**/playwright.config.*`, `angular.json`, `**/jest.config.*`,
  `**/vitest.config.*`, `**/karma.conf.*`, `pytest.ini`, `tox.ini`, `.pre-commit-config.yaml`,
  `.husky/**`, `**/.npmrc`, `**/global.json`, `.tool-versions`, `.mise.toml`) plus one content
  rule (a `package.json` hunk adding `preinstall`/`postinstall`/`prepare`), and
  `ENV_DECL_GLOBS` (`devclaw.json`, `.devcontainer/**`). In-scope paths: backticked paths or
  globs in the issue text that match a gate-input glob reclassify to `product` for that
  task. Existing `_diff_stats` binary counting is replaced by the `paths` projection.
- `_ChangeClassGate` (`gate_id="change_class"`, in `ALWAYS_HARD`) after `_MaterializeGate`
  in both chains: fails on any `gate_input` path not in scope and on any binary, naming
  them and the two legitimate moves. The failure carries a fast-fail marker (like
  `_PROMPT_TOO_LONG_MARKER`) so the retry loop does not re-run the identical span.
  `env_decl` edits pass and emit a `env_declaration_changed` goal-log line.
- Evidence: `_done_gate_review_brief` (`tick_donegate.py:67`) gains one line naming the
  gate-input classes as non-evidence; `devclaw/prompts/goal-evaluator.md` step 2 gains the
  same rule (presence + absence asserted by extending
  `test_done_gate_review_brief_forbids_existence_only_test_evidence`).
- Skills: `50-repo-gate-conflict.md` is rewritten around the two typed hand-backs; the
  `--no-verify`/`SKIP=` sentence and the "document WHY in AGENTS.md" step are removed;
  `20-verify-gate-coverage.md:9` points the verify declaration at `devclaw.json`
  (`verifyCmd`, an env-declaration edit) and never at the CI workflow; `90-commit.md:23`
  states that gate inputs and binaries fail the task. A structural guard in
  `tests/test_runner_skills.py` asserts the bundle contains no `--no-verify`.

**Alternatives.** Classify in `change_advisories.py` — rejected: that module is advisory
by contract; a failing classification is a gate. Ask the reviewer to spot gate-input edits
— rejected: that is the diary-reading root. Content-scan every file for "sandbox" prose —
rejected: policy-as-keyword-list.

## R5 — The metric counts four verbs and non-worker commits from the store

**Finding.** `compute_scorecard` (`telemetry.py:341`) already counts `human_steers` from
`goal_steering.source NOT LIKE 'auto-%'` (`:592-610`); `goal_decisions` carries `verb` and
`provenance='owner'`; `goal_convergence` is the achieved denominator (`state.py:157`);
`resume_goal` deliberately writes no steering row (`service.py:1628`); worker commits carry
the pinned identity from `git_identity_env()` (`git_identity.py:42`).

**Decision.** One table `goal_interventions(id, goal_id, verb, ref, made_at)` written by the
four verbs in `GoalService` (`steer`, `resume`, `decide`, `correct_implementation`) and by
delivery for each commit in `base..HEAD` whose author email is not the pinned identity
(`verb="commit"`, `ref=<sha>`) — delivery already walks `base..HEAD` for subjects
(`delivery/__init__.py:432`). `compute_scorecard` gains `interventions{steers, resumes,
decisions, non_worker_commits, achieved_goals, per_achieved_goal, items[]}`; `items` lists
goal id + verb + ref. Doctor: `instance.scorecard.goal_interventions` table check + seeded
fault. `format_scorecard` and the CLI print the ratio.

**Alternatives.** Derive resumes from `goal_log` prose — rejected: views are never read
back for decisions (Principle IV). Reuse `goal_steering` with `source="resume"` — rejected:
`resume_goal`'s contract is "records no steering".

## R6 — Q3 lands at admission, not only at the gate

**Decision.** The done-gate's `no_workflows` outcome is the backstop; the primary is
admission: spec 030's capability set gains `ci:definition` (a project's default branch
carries at least one `.github/workflows/*.yml`), probed on the sweep cadence like the other
two ids, declared implicitly for every project (Q3: not dispatchable without CI). Onboarding
(`runner/skills/onboard/00-onboard.md`) adds the CI workflow as a sibling artifact of
`.devcontainer/Dockerfile` — a skill edit, no engine work.

## R7 — Doctrine amendments (FR-013/014)

Constitution Principle V: replace "the human reviews merged work after the fact with
revert as the remedy" with "the project's own verification environment — its CI, read as
a mechanical fact for the exact head — is the verdict of record; the validation lane is
the backstop; the human is not a stage". Bump to 2.6.0. `CLAUDE.md:132-133` and
`docs/architecture.md:205` drop "post-merge human review is the backstop";
`docs/reference/env-vars.md` retires `DEVCLAW_GOAL_CI_GATE`; `docs/INDEX.md` currency tags;
`.sandcastle/Dockerfile:121` comment rewritten as a declared-environment note; spec 030's
status line records that 032 generalizes its capability set.

## R8 — US4 is designed only to the interface (Q1 = C)

The manifest gains an `environment` block (`image`, `services[]`, `tools[]`,
`registries[]`) in the schema and `parse_manifest` (absent ⇒ today's behavior; malformed ⇒
loud, the `validation` block precedent at `project_manifest.py:178`). Admission maps each
declared item to a capability id (`tool:<name>`, `service:<name>`, `registry:<name>`);
provisioning of `tools` rides mise in the runner pre-step (ADR 0005); `services` are
host-side sibling containers reachable over the sandbox's host networking. Implementation
is planned in a follow-up revision of this plan after US1–US3 have a live track record;
this plan only ships the schema/parse surface so declarations can start accumulating.
