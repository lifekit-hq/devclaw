# Plan: Intake Readiness Gate (spec 006, P1)

## Integration seam

- **`file_intake` stays unchanged** in what it returns — it files the issue and
  returns the receipt synchronously (FR-001/FR-011). Zero cognition on this path.
- The **async grade attaches in the MCP tool** (`server/tools.py::file_intake`):
  after the receipt is produced, `_schedule_readiness_grade(...)` fires a
  background `asyncio.create_task` running `intake.grade_and_label(...)`. Strong
  refs are held in a module-level `_GRADE_TASKS` set so the loop doesn't GC them.
  The grade landing later as a label is the "async" clarification made concrete.
- **Re-trigger** (FR-010) is a new MCP tool `regrade_intake(project_id, issue_url)`
  → `intake.regrade(...)`, which reads the (amended) issue body on demand, parses
  the ask back out, and swaps the readiness label. No edit-watching.

## New modules

- **`devclaw/intake_readiness.py`** — the cognition caller, shaped like
  `goal/evaluator.py`: `build_prompt` / `extract_json` / `validate` / `evaluate`,
  plus a `repo_context` snapshot wrapper (`asyncio.to_thread` over
  `task_git._review_repo_context_sync`, module global, best-effort, never-raises)
  and a `default_caller()` bound to the new `intake_readiness` model tier
  (STANDARD). Returns a parsed `ReadinessVerdict(ready, missing, rationale)`; may
  raise `ReadinessError` on unusable output.
- **`devclaw/prompts/intake-readiness.md`** — grades ONLY groundability (locatable
  surface + concrete change + verifiable intent). #227 grounding clause; refers to
  "Repository context" without the `##` in instruction text; explicitly does NOT
  derive `done_when`/checklist (FR-006 non-overlap). `done_when` enters as
  grounding context, marked "NOT a checklist to grade."

## Label-transition model (FR-007 — the label is source of truth)

- Pending → (no readiness label; treated as not-ready).
- Grade → `devclaw-ready` XOR `needs-refinement` added; the opposite readiness
  label removed (so a re-grade flips cleanly). The existing `devclaw-intake`
  label is retained. A mirror comment states the verdict/missing elements; it is
  never read back for decisions.

## Fail-closed (FR-005) — the choke point lives in `intake.grade_and_label`

The cognition caller may raise; the **orchestrator catches everything and lands
`needs-refinement`, never `devclaw-ready`**:
- empty repo snapshot → short-circuit to not-ready with a distinct
  "couldn't read the repo" reason (FR-008), *before* spending a token;
- evaluator crash / usage-limit pause (surfaces as an exception) → not-ready;
- malformed / non-JSON / `ready` not explicitly true → not-ready.
`grade_and_label` never raises (gh write failures are logged, not propagated).

## Zero-token idle guard (FR-009)

The grade is scheduled on the intake tool path only; `tick.py` and the heartbeat
are untouched. Guarded by `test_readiness_grade_adds_no_idle_tick_cognition`
(no tick-path module references the readiness gate) + the existing idle
zero-token tests staying green.
