# Implementation Plan: A stop needs evidence

**Branch**: `044-a-stop-needs-evidence` (spec + plan) · implementation lands as three stacked increments `feat/a-stop-needs-evidence` (US1) · `-us2` · `-us3` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

## Summary

Layer 2 and layer 3, one new leaf. **US1**: `Problem` gains one field, `evidence` (a typed citation + the check that proved it), and every raise site fills it from a snapshot it already holds; a new pure module `devclaw/goal/evidence.py` parses the citation shapes and runs the named checks (no cognition, no network, one bounded `git ls-tree` where a tree is the snapshot). A stop whose claim has no passing citation is an *unproven stop*: under `trust` the seam records a `dropped_claim` catalog row and proceeds; under `strict` it raises the Problem with `evidence.proven=false` (rendered *no evidence*) and a `drop` option as the recommended default. The four instance fixes ride as US1 tasks: the intake staleness citation (#896), the strict env-report Problem over a green probe (#891 landed the trust half), the manifest `verifyCmd` refusal (#897), the declared-scope match against the classified path (#895). The dispatch-cap Problem's `what` carries the last failure's mechanism (FR-010). `Decision` gains `missed_stage` and `list_problems` gains a `design_stops` grouping (FR-017). The structural guard (`tests/test_mechanical_blocks_are_recheckable.py`) is extended to read every `new_problem(` call and fail one without `evidence=`. Constitution V gains the clause. **US2**: `evaluator.validate` returns `achieved` once every clause is satisfied with evidence, in both modes; structural findings ride `_close_and_merge` as follow-ups (they already do under `trust`). **US3**: `_review_failure` drops blockers whose `location` is neither in the span nor in the pre-run tree, records them as dropped, and returns `None` with advisories when no blocker survives.

## Technical Context

**Language/Version**: Python 3.12 (existing) · **Primary Dependencies**: none new (stdlib `json`, `re`, `subprocess` for one `git ls-tree`) · **Storage**: SQLite `devclaw.db` — one `ALTER TABLE goal_problems ADD COLUMN evidence_json`, one `ALTER TABLE goal_decisions ADD COLUMN missed_stage`, both in `goal/state.py`'s migration list, each with a doctor check (spec 016 FR-014) · **Testing**: pytest, stubbed; tripwire classes only (fail-closed gate, structural guard, doctor seeded-fault, zero-token) · **Target Platform**: the VPS instance, Linux · **Project Type**: modular monolith, layers 2–3 · **Performance Goals**: zero added tick-path cognition; at most one git subprocess per consulted seam · **Constraints**: import direction (`lint-imports`): `goal/evidence.py` imports nothing above `goal/`; `task_change.py` and `intake_readiness.py` stay leaves · **Scale/Scope**: 7 raise sites, 3 gates, 1 grader, 1 admission check, 2 columns, 1 prompt field, 2 prompt lines

## Constitution Check

- [x] **I. OAuth only** — no spawn site touched; the one new subprocess is `git`.
- [x] **II. Model-agnostic worker layer** — no skill or hook changes; the worker's `BLOCKED:` protocol is read, not rewritten. FR-017's two instruction lines land in host prompts (`intake-readiness.md`) and the `dispatch-ready` skill, not in `runner/skills/`.
- [x] **III. Zero-token idle** — every check runs inside a seam that already spent its call (the grader, the review gate, the settle, the done-gate) or on a mechanical path (admission, the cap, the skip). The strict no-evidence Problem is store rows + one notify. `FakeClaude.calls == 0` on blocked ticks stays pinned.
- [x] **IV. Single writer** — `evidence` is written in the same `raise_problem` transaction as the BLOCK; `missed_stage` in `resolve_problem`'s Decision transaction; `dropped_claim` rows go through `record_problem` (best-effort, never a decision input). No view is read back.
- [x] **V. Fail-closed verification / done is a proposal** — **amended in this arc (2.11.0)**: the uncited-pass direction is unchanged (an approve without a citation, an `achieved` without clause evidence, still fail closed); the uncited-STOP direction is declared: it fails open to the fact under `trust` and is judged under `strict`; the structural axis no longer holds a close once every pinned clause is met (US2); a review refusal blocks only through a location inside the change (US3). Owner Decisions are the sole exception. The evaluator stays the only machine `achieved` emitter.
- [x] **VI. Loud failure** — every drop is a catalog row + a log line + (intake) a comment + (review) a PR advisory; every Problem renders its citation or the words *no evidence*; the structural guard names the raise site that ships without evidence.
- [x] **VII. Fix the class** — the class is "a claim given the authority of a fact"; the rule is one field checked at one seam; the six instances become citations at their seams. No seam registry (rejected at clarify).
- [x] **VIII. Cognitive guardrail?** — none added; two removed. The strict structural hold (US2) and the fail-closed uncited refusal (US3) were compensations for judge error; their A/B seam was the strictness dial and their instrument is `donegate_churn` parks with all clauses met and refusals citing only absent locations. The evidence rule is a structural invariant (a protocol field), not a classifier — never shed, no A/B.
- [x] **IX. Instruct thin, verify thick** — new Python serves the **protocol** (what a stop must carry) and the **verdict of record** (a fact outranks a claim); it encodes no project-code knowledge (paths are checked for existence in the project's own tree, never interpreted). Gaps closed as a *fact* first: the tree file list handed to the grader, the probe row to the env seam, the manifest to admission, the ticket's own tokens to the scope rule. The only instruction change is FR-017's two lines. Standard practice: the shape is a citation + a lookup, the way a linter cites a line.

## Project Structure

### Documentation (this feature)

```text
specs/044-a-stop-needs-evidence/
├── plan.md              this file
├── research.md          decisions per seam; the deviations stated
├── data-model.md        Evidence, Problem.evidence, Decision.missed_stage, dropped_claim rows
├── quickstart.md        tripwires + the live proof
├── contracts/
│   ├── seams.md         the seam table: claim → citation → check → snapshot → unproven direction
│   ├── mcp-and-http.md  read/verb shape changes (get_goal, decide, list_problems, create_goal, set_goal_verify_cmd, regrade_intake)
│   └── prompts.md       the one prompt field (intake stale_evidence), the two FR-017 lines, the evaluator wording
└── tasks.md             /speckit-tasks output (not created here)
```

### Source code (repository root)

```text
devclaw/goal/evidence.py          NEW leaf: Evidence dataclass, citation grammar, the named checks (pure; one optional git ls-tree helper)
devclaw/goal/models.py            Problem.evidence: Evidence | None; Decision.missed_stage: str; Evidence, DROP option key
devclaw/goal/problems.py          new_problem(evidence=...) required kw; DROP / PROCEED options; render shows the citation or "no evidence"; to_dict gains "evidence"
devclaw/goal/state.py             two ALTER TABLE lines (evidence_json, missed_stage)
devclaw/goal/state_problems.py    row I/O for both columns
devclaw/goal/decisions.py         render: a design Decision's missed stage
devclaw/goal/tick.py              readiness skip: Problem cites the grade (grade:) ; the unproven-stop helper (drop under trust / no-evidence Problem under strict)
devclaw/goal/tick_dispatch.py     cap Problem: what = last failure mechanism; evidence report:<task_id>
devclaw/goal/tick_settle.py       worker_block: design stop → evidence report:; env report → probe check, strict Problem
devclaw/goal/tick_guards.py       _block_on_env_deficiency: superseded-green → drop (trust) / Problem (strict)
devclaw/goal/tick_donegate.py     needs_human / churn / ci-definition Problems carry evidence (clause: / report: / manifest:)
devclaw/goal/evaluator.py         US2: all clauses satisfied ⇒ achieved in both modes; structural rides as follow-ups
devclaw/goal/admission.py         #897: manifest verifyCmd refuses a goal verify_cmd; bare tool name refuses
devclaw/goal/service.py           #897 in set_verify_cmd; resolve_problem(missed_stage=); list_problems design_stops grouping
devclaw/intake_readiness.py       #896: stale_evidence field, tree-list check; repo_context sibling tree_files()
devclaw/intake.py                 stale comment names the citation or the missing evidence; dropped_claim row
devclaw/prompts/intake-readiness.md   stale_evidence in the schema; FR-017 grounding line
devclaw/task_change.py            #895: in_scope_from_text keeps path-shaped tokens; build_paths matches classified gate-input paths
devclaw/task_queue.py             US3: _review_failure drops out-of-span blockers; approve-with-advisories
devclaw/quality/__init__.py       US3: filter_blockers(review, span_paths, tree_paths) — pure
devclaw/queue/settle.py           US3: dropped findings attached as gate advisories (existing _attach_gate_advisory)
devclaw/state_store/problems.py   DROPPED_CLAIM_KIND constant (category "gate")
devclaw/server/tools/goals.py     decide / correct_implementation gain missed_stage; docstrings for create_goal / set_goal_verify_cmd
devclaw/server/tools/observability.py   list_problems: design_stops section
devclaw/server/routes/goals.py    problem read shape carries evidence
devclaw/doctor/checks_instance.py check_goal_problems_evidence_column, check_goal_decisions_missed_stage
console/src/pages/GoalDetail.tsx  render evidence / "no evidence" on the banner
~/.claude/skills/dispatch-ready/SKILL.md   FR-017: one grounding line — OUTSIDE this repo (user-level skill on the PC); an implement task edits it by hand, it is not part of the PR
.specify/memory/constitution.md   V amended (2.11.0)
tests/test_mechanical_blocks_are_recheckable.py   extended: every new_problem( passes evidence=; every Problem-raising site
tests/test_goal_evaluator.py      strict structural hold test retired (symmetric ratchet); all-clauses-met ⇒ achieved pinned in both modes
tests/test_review_gate.py         filter_blockers: out-of-span-only refusal approves with advisories; in-span blocker still fails closed
tests/test_intake_readiness_fail_closed.py   stale without evidence / with an absent path grades not stale
tests/test_task_change.py         declared package.json in scope (extends the spec 032 US3 case)
tests/test_env_cap_admission.py   strict: green-probe report raises a Problem citing the probe; trust: no hold, dropped_claim row
tests/test_doctor.py              seeded-fault pair for each new column
tests/cognition/fixtures/intake_readiness/fs493_stale_uncited.json   the fs#493 fixture (mechanism guard: validate() with no citation ⇒ not stale)
docs: CLAUDE.md (invariants bullet), docs/architecture.md (Problems paragraph, gate section), docs/reference/intake-shape.md, docs/flows/task-execution.md (review hop), devclaw/quality/README.md, docs/INDEX.md currency tags
```

**Structure Decision**: no new package. `goal/evidence.py` is a leaf inside `goal/` so `tick*`, `service`, `evaluator` and `problems` can import it without an upward edge; `intake_readiness.py` and `task_change.py` (both leaves per `[tool.importlinter]`) do their own existence checks over inputs the caller passes and do not import `goal/`. The review filter lives in `quality/__init__.py` beside `validate_review` because it is a verdict normalisation, and `task_queue._review_failure` calls it with the span and tree it already can read.

## Complexity Tracking

No constitution violation to justify. The one deviation from the spec's letter is stated in research.md (the intake seam has no goal, so no strict Problem can be raised there; the drop is unconditional and the comment is the owner-visible surface).
