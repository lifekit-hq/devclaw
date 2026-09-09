# Research: A stop needs evidence (Phase 0)

No NEEDS CLARIFICATION remained after the four clarify rulings. This file
records the design decisions the plan rests on, each with the alternative
rejected, so the tasks phase inherits the reasons and not only the shapes.

## D1. Where the check lives: one pure leaf, callers pass the snapshot

- **Decision**: `devclaw/goal/evidence.py` holds the citation grammar and
  the named checks as pure functions over inputs the CALLER already holds
  (a set of tree paths, the pinned clause ids, the registry probe ids, the
  manifest keys, the decision ids, the run's task id). The module never
  fetches; the one helper that touches git (`tree_paths(dir, sha)`) is a
  separate best-effort function the caller invokes only inside a seam that
  already spent its call.
- **Rationale**: constitution III (zero tick-path cognition or subprocess on
  idle/blocked paths) and the cognition-prompts rule that snapshot
  collectors are never-raises and live at the call site. A checker that
  fetched its own snapshot would be a second collector per seam.
- **Alternatives rejected**: a check method on `Problem` (models are frozen
  data, and the check needs a snapshot the model does not carry); a registry
  mapping seam → checker (the clarify-rejected registry by another name).

## D2. The citation grammar: `<shape>:<ref>`, six shapes, one parser

| shape | example | check | snapshot |
|---|---|---|---|
| `path` | `path:backend/src/…/CredentialEncryptionService.cs:35` · `path:src/x.py#Foo.bar` | `path_in_tree` — the path (before `:line` / `#symbol`) is in the tree list | a file list (`git ls-files` at the graded head, or `git ls-tree -r --name-only <base_sha>` for the pre-run tree) |
| `clause` | `clause:c3` | `clause_pinned` — the id is in the active pin's clause ids | `store.active_pin(goal_id).clauses` |
| `probe` | `probe:registry:npm-github` | `probe_registered` — the id is a `CAP_*` id or a `worker:` id with a superseding capability | `env_cap.CAP_SCOPES` + `_SUPERSEDING_CREDENTIALS` |
| `manifest` | `manifest:verifyCmd` | `manifest_declared` — the key is set in the project's manifest at the merged base | `project_manifest.load_manifest_at_base` |
| `decision` | `decision:dec_…` | `decision_recorded` — a current Decision with that id exists | `store.decisions(goal_id)` |
| `report` | `report:<task_id>` · `report:grade:<repo>#<n>` | `report_present` — the increment record / the intake grade row exists | `store.increment_records` · `store.intake_grade` |

- **Rationale**: the spec names five shapes; `report` is the sixth the "Two
  kinds of stop" ruling requires — a design stop's evidence IS the worker's
  finding, "checkable as present in the run", and a readiness revocation's
  evidence is the grade that revoked it. Existence and shape only, never
  truth (spec edge case 1).
- **Alternatives rejected**: free text with a regex per seam (six regexes
  = six mechanisms); a URL to the artifact (the check would need the
  network).

## D3. The unproven-stop rule is one helper, not six branches

- **Decision**: `tick.py` gains `_unproven_stop(goal, status, *, seam, claim,
  evidence, store, notifier, options, ...)` returning either `None` (trust:
  a `dropped_claim` catalog row + a log line were written; the caller
  proceeds) or the blocked status (strict: a Problem with
  `evidence.proven=False`, options = the caller's options prefixed by
  `DROP` — "drop the claim and proceed" — as the recommended default,
  the standard timebox). Every seam that turns a claim into a stop calls
  it; the strictness is `project_manifest.resolve_goal_strictness(goal)`,
  the same read the done-gate uses.
- **Rationale**: the clarify ruling "one policy on the existing dial"; a
  second branch per seam is the drift the spec removes.
- **Alternatives rejected**: fail every unproven stop closed (rejected in
  the spec); per-seam `if strictness == "strict"` blocks (six copies of one
  rule).

## D4. Which seams are claims, which are facts (the seam table is `contracts/seams.md`)

- **Claims subject to FR-003** (a judgment that stops): the intake `stale`
  verdict; the worker `env —` report when a probe exists; the pre-PR review
  refusal's blockers; the done-gate's structural axis (removed by US2 rather
  than checked).
- **Facts that need no drop rule, only a citation**: the dispatch cap (a
  counter; evidence = the last failure record); the churn park (rounds
  flat; evidence = the unsatisfied pinned clause); the CI-definition Problem
  (a remote fact; evidence = `manifest:ci` / the rollup state); a design
  stop (`report:<task_id>`, always proven by construction, FR-016); the
  readiness-revoked park (`report:grade:…`, or the owner's own label pull).
- **Refusals at the boundary, not stops**: a goal `verify_cmd` under a
  manifest `verifyCmd` (#897) and a bare tool name are refused at admission
  with the manifest key named; nothing persisted, no Problem.
- **Rationale**: the spec's "Two kinds of stop" plus edge cases 4 and 5. A
  fact cannot be "unproven"; only text can.

## D5. The intake seam has no goal — the drop is unconditional there

- **Decision**: a `stale` verdict with no `stale_evidence`, or with a path
  absent from the graded tree, grades as NOT stale in every project,
  regardless of `strictnessDefault`. The posted comment names the missing
  or unfound citation; a `dropped_claim` row (seam `intake_staleness`) is
  recorded; the `intake_grades` row keeps `stale=False`.
- **Rationale**: FR-003's strict branch raises a Problem, and a Problem is
  a goal's; at grading time there is no goal to block. The comment IS the
  owner-visible surface, and the direction is the one spec 028 FR-010
  already takes ("uncertain means not stale"). This is the plan's one
  stated deviation from the spec's letter; it is inside its intent (SC-003:
  the stale grade that stranded issue-493 becomes a dropped claim, never a
  skip).
- **Alternatives rejected**: label the issue `needs-refinement` and let a
  strict pointer goal raise the Problem at dispatch — that is the silent
  skip the spec kills, one hop later.

## D6. The tree list for the grader: extend the collector, do not add one

- **Decision**: `intake_readiness.repo_context(workspace_dir)` keeps its
  rendered text; a sibling `tree_files(workspace_dir) -> frozenset[str]`
  (best-effort, never raises, `""`/empty on a git hiccup) returns the
  `git ls-files` set the collector already runs. `intake.grade` passes it
  to `validate(parsed, tree=...)`. An empty tree means no citation can
  pass, which is the safe direction (not stale).
- **Rationale**: `_review_repo_context_sync` already runs `ls-files` and
  keeps only the top-level names; the file list is the fact #896 needs, at
  zero new subprocesses.
- **Alternatives rejected**: render the whole file list into the prompt
  (token cost on the least reliable call class); a second `git` call per
  grade.

## D7. Review refusal filtering (US3): normalise, then consult

- **Decision**: `quality.filter_blockers(review, span_paths, tree_paths)`
  is pure: a blocking issue whose `location`'s path prefix (text before
  `:`, `#`, ` ` or `(`) matches neither a span path nor a tree path is
  moved to `review["dropped"]`, severity kept; `blocking` is recomputed;
  `verdict` follows `blocking` (the existing reconcile rule). A location
  with no path-shaped prefix at all (prose) counts as absent — the prompt
  already demands `<file path and function/area or line>`. `_review_failure`
  reads the span from `GateInput.change()` (already computed) and the tree
  via `evidence.tree_paths(workspace_dir, base_sha)`; it returns `None`
  when nothing blocks and hands the dropped list to the settle, which
  attaches it through the existing `_attach_gate_advisory` so the PR body
  shows it (ADR 0007 surface).
- **Rationale**: clarify ruling "approve with advisories"; the check is a
  set membership over facts the gate already has.
- **Alternatives rejected**: re-review once (rejected at clarify); ask the
  model to cite paths only from a provided list (a prompt cannot enforce
  it — constitution IX puts the enforcement in Python).

## D8. US2: the verdict is the clauses, structural is advice — in `validate`

- **Decision**: in `evaluator.validate(at_done_gate=True)`, once every
  clause is satisfied with non-empty evidence and no stub disguise applies,
  the verdict is `achieved` for both dial positions; `structural_health` /
  `structural_concerns` are preserved on the result and `_close_and_merge`
  records them as follow-ups (existing `followups=` path). The
  `off_track`-with-no-corrections-but-structural-concerns branch (line
  ~627) is folded into the same rule. The prompt's line "the host applies
  the goal's strictness dial to it" is corrected to "the host records it
  as follow-ups on the close".
- **Rationale**: clarify ruling "advisory in both modes"; the bar is the
  pinned contract + CI (spec 032/035).
- **Alternatives rejected**: pin concerns per revision; one correction
  round (both rejected at clarify).
- **Symmetric ratchet**: `test_done_gate_structural_concerns_still_block_under_strict`
  and the strict half of `test_done_gate_taste_corrections_cannot_hold_a_met_contract_open`
  are removed in the same PR; the trust cases become the both-modes case.

## D9. FR-010: the cap Problem's `what` carries the mechanism

- **Decision**: `tick_dispatch` reads the last increment record with an
  error and, in `what`, states `last failure: <gate id>: <first line>` —
  the settle already writes `verify gate failed (exit N): <cmd> …`,
  `change_class: gate-input edit(s) <paths>`, `review gate …` as the task
  error's head, so the first line IS the mechanism; no new parsing. The
  Problem's evidence is `report:<task_id>` of that record.
- **Rationale**: the owner's `continue` should never be blind; the fact is
  already on the row.

## D10. FR-017: `missed_stage` on the Decision, `design_stops` on `list_problems`

- **Decision**: `Decision.missed_stage: str` (`intake_grade` | `admission`
  | `plan` | `none` | `""` = not recorded), a column on `goal_decisions`;
  `decide` / `correct_implementation` accept an optional `missed_stage`;
  `render_for_human` on a design Problem (evidence shape `report:` from
  `worker_block` or `done_gate`) asks for it in one line. `list_problems`
  gains a `design_stops` object: `{"<stage>": [{goal_id, problem_id,
  decision_id, what, made_at}], "unrecorded": [...]}` over resolved design
  Problems in the window — an omitted stage is visible as `unrecorded`,
  never silently `none`. The two instruction lines: `intake-readiness.md`
  grounding section ("ground the ask against the world it depends on —
  provider capabilities and prior findings recorded in the repository —
  not only against the code") and the same sentence in the
  `dispatch-ready` skill's grounding step.
- **Rationale**: the ruling that a design stop reaching the owner is a
  planning or grading defect; making the stage a required parameter would
  add owner work on every decide (a defect under the 2026-09-07 ruling), so
  it is optional and its absence is counted instead.
- **Alternatives rejected**: derive the stage mechanically (a judgment — the
  model's or the owner's, never Python's, constitution IX); a required
  parameter.

## D11. `dropped_claim` rows ride the existing catalog

- **Decision**: `record_problem(category="gate", kind="dropped_claim",
  message=f"{seam}: {claim[:200]} — cited {citation or 'nothing'} ({check})",
  recovered=True, goal_id=…)`. Fingerprint dedups per seam + normalised
  claim; `count` is the cut-condition number, read with
  `list_problems(category="gate")`. `DROPPED_CLAIM_KIND` lives in
  `state_store/problems.py` beside `ENV_DEFICIENCY_KIND` (the leaf both
  layers import).
- **Rationale**: FR-014 "countable … read from list_problems, never
  remembered"; a new table would be a second catalog.

## D12. The guard extension reads `new_problem(` calls, not kinds

- **Decision**: `tests/test_mechanical_blocks_are_recheckable.py` gains a
  second AST scan: every `new_problem(` call in the package passes an
  `evidence=` keyword (a missing keyword fails the build naming file:line),
  and every `store.transition(... Event.BLOCK ...)` whose status literal
  carries `problem_id=` sits in a module that imports `problems` (a block
  that parks with a `blocked_kind` outside the healable set and no Problem
  is the silent stop FR-001 forbids — the existing kind guard already
  covers `mechanical:*`; this covers `needs_answer` / `donegate_churn` /
  `env`). `new_problem` itself makes `evidence` a required keyword so the
  runtime agrees with the guard.
- **Rationale**: the spec's independent test for US1 scenario 7; the
  existing guard is structural on purpose (it caught kinds no test
  exercised) and this is the same shape one level up.

## D13. Doctor checks for the two columns

- **Decision**: `check_goal_problems_evidence_column` and
  `check_goal_decisions_missed_stage` follow `check_goal_status_ci_green_head`'s
  shape (PRAGMA table_info; FAIL naming the column and "restart applies
  the migration"); seeded-fault pair each in `tests/test_doctor.py`.
- **Rationale**: spec 016 FR-014 — a persisted-shape change ships its
  doctor check.
