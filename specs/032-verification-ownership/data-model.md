# Data model — spec 032 Verification ownership

Phase 1 output. Entities are the spec's Key Entities made concrete against the existing
store (`devclaw/goal/state.py`, `devclaw/state_store/schema.py`). Every persisted change
below ships its doctor check and seeded-fault test (FR-014 of spec 016).

## 1. Check rollup fact (in-memory value, never cached across heads)

`RemoteChecksResult` (`devclaw/goal/remote_checks.py`) — extended, frozen dataclass.

| field | type | meaning |
|---|---|---|
| `state` | `passing \| failing \| pending \| infra_broken \| no_workflows \| no_pr \| unknown` | the fold over required checks (`none` folds into `no_workflows`; `no_pr` = the branch has no PR, a no-change goal) |
| `head_sha` | str | the PR head the rollup describes; `""` when unknown |
| `pr_url` | str | the PR the rollup was read from; `""` for no_pr / unknown |
| `failing_names` | tuple[str, …] | required checks with a bad conclusion |
| `pending_names` | tuple[str, …] | required checks still queued/in progress |
| `detail` | str | one human line, ≤ 200 chars in logs |

Validation: `state == passing` requires `failing_names == () and pending_names == ()` and a
non-empty `head_sha`. `blocks_done(mode)` is removed (the mode dial is retired); the
consumer switches on `state` directly.

## 2. Goal status additions (`goal_status`, owned by `GoalStore`, CAS'd)

| column | type | default | written by | cleared by |
|---|---|---|---|---|
| `pending_done_proposal` | INTEGER (bool) | 0 | `_open_done_gate` on `pending`/`unknown` hold | `ACHIEVE`, cancel, the re-opened gate's outcome, productive settle |
| `ci_green_head` | TEXT | `''` | `_open_done_gate` on `passing` | same as above |

State transitions touched (all already in `LEGAL`): `(EXECUTING_IDLE, BLOCK) → BLOCKED`
for the `mechanical:ci` hold raised from `_open_done_gate`; `(BLOCKED, RESUME_IDLE) →
EXECUTING_IDLE` for the heal; `(BLOCKED, BLOCK) → BLOCKED` for a re-hold on a moved head.
New `blocked_kind` value: `mechanical:ci` (added to the taxonomy comment at
`models.py:215-223` and to `test_blocked_kind_stamped_per_block_site`).

Doctor: `instance.ci.goal_status_pending_done_proposal`,
`instance.ci.goal_status_ci_green_head` via `_goal_status_column_finding`.

## 3. Environment deficiency (worker-reported capability row, `meta` table)

Key: `env_cap_probe:worker:<slug(item)>@<project_id>` — the spec-030 row shape so admission,
the hold, the heal, doctor and `get_goal` all tell one story.

| value field | type | meaning |
|---|---|---|
| `status` | `red \| green` | red while the instance's `env_ref` equals the recorded one |
| `evidence` | str | the worker's item text plus goal/task ids |
| `remedy` | str | "provide `<item>` in the sandbox (image, mise tool, or declaration) — devclaw work" |
| `env_ref` | str | `<sandbox image ref>|<DEVCLAW_GIT_SHA>|<sha256(devclaw.json)>` at report time |
| `probed_at_ms` | int | report time |

Rules: written once per (item, project); a repeat report refreshes `probed_at_ms` only;
`red_caps_for(store, declared, project_id)` returns `worker:*` rows for the project whether
or not declared; `refresh_needed` never schedules a probe for them; a row whose `env_ref`
differs from the current instance value reads as green. The problems catalog row is
`category="block"`, `kind="env_deficiency"`, `message=<item>` (`record_problem`), so the
fingerprint is per item and `terminal_count` makes it self-issue eligible.

## 4. Changed path classification (`ChangeSet.paths`)

`ChangedPath` (frozen dataclass in `devclaw/task_change.py`):

| field | type | meaning |
|---|---|---|
| `path` | str | repo-relative path (rename target for renames) |
| `status` | `A \| M \| D \| R \| T` | git name-status letter |
| `binary` | bool | `--numstat` reported `-\t-` |
| `cls` | `product \| gate_input \| env_decl` | from `classify_path` |
| `in_scope` | bool | a gate-input path reclassified to product by the issue text |

`ChangeSet.paths: tuple[ChangedPath, …]` (default `()` so every existing stub stays valid,
e.g. `tests/test_task_retry.py:551`). Derived projections: `gate_input_paths`,
`binary_paths`, `env_decl_paths`. Invariant: the classification is computed once in
`_capture_change` and read by the gate, delivery and the done-gate brief; no consumer
re-derives it (spec 013's rule extended to the class).

## 5. Intervention (`goal_interventions`, new table, one writer: `GoalStore`)

| column | type | meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `goal_id` | TEXT | indexed |
| `verb` | `steer \| resume \| decide \| correct_implementation \| commit` | |
| `ref` | TEXT | decision id, steering row id, or commit sha |
| `made_at` | TEXT (ISO) | indexed for the window query |

Writers: the four `GoalService` verbs; delivery (`verb="commit"`) for each `base..HEAD`
commit whose author email ≠ `git_identity_env()["GIT_AUTHOR_EMAIL"]`. Reader:
`compute_scorecard` → `interventions` block; denominator = `goal_convergence` rows with
`outcome='achieved'` and `closed_at` in the window. Doctor:
`instance.scorecard.goal_interventions` (table present) with a seeded `DROP TABLE` fault.

## 6. Capability id additions (spec 030 registry)

`ci:definition` — green when the project's default branch carries at least one
`.github/workflows/*.yml` (one `gh api contents` read on the sweep cadence); implicitly
declared for every registered project (Q3). Doctor's `project.capabilities.undeclared`
advisory is unchanged.

## 7. Manifest `environment` block (schema + parse only in this arc, R8)

```json
"environment": {
  "image": "mcr.microsoft.com/dotnet/sdk:10.0",
  "services": ["postgres:14"],
  "tools": ["dotnet-ef@10"],
  "registries": ["npm-github"]
}
```

Absent ⇒ `Manifest.environment is None` (today's behavior). Malformed ⇒ `ManifestError`
(loud, the `validation` block precedent). Consumed by nothing until US4's follow-up plan.
