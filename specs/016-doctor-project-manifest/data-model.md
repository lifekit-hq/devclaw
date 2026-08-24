# Data Model: Instance Doctor + Per-Project Manifest (spec 016)

## Doctor report (in-memory only — never persisted)

### `Verdict` (str enum)
`ok` | `warn` | `fail` | `unknown`

- `unknown` = the check could not execute; the error is the evidence
  (fail-loud, never omitted).

### `Finding` (frozen dataclass)
| field | type | notes |
|---|---|---|
| `check_id` | str | stable, e.g. `instance.migrations.meta_keys`, `project.links.dangling` |
| `verdict` | Verdict | |
| `evidence` | str | human-readable, state-derived, no timestamps |
| `remedy` | str | existing verb name for non-ok verdicts (`link_goal`, `resume_goal`, `clear_usage_pause`, `onboard`, `vps-relogin`, `set_run_schedule`); empty for `ok` |
| `project_id` | str \| None | None for instance-section findings |

### `DoctorReport` (dataclass)
| field | type | notes |
|---|---|---|
| `healthy` | bool | True iff every finding is `ok` |
| `findings` | list[Finding] | fixed order: instance checks in declared order, then per-project sorted by project id |

Serialized by the MCP tool / CLI as
`{"healthy": bool, "counts": {"ok": n, "warn": n, "fail": n, "unknown": n}, "findings": [...]}`.
Deterministic: identical state ⇒ byte-identical JSON.

## Check registry (code-level, `devclaw/doctor/`)

Each check is a named function `(deps) -> list[Finding]`, registered in a
module-level ordered tuple. Instance checks receive `(store, goal_store,
registry)`; project checks receive `(project, goal_store, all_goals,
manifest_or_error)`. Adding a check = appending to the tuple + a seeded-fault
test (the FR-014 rider).

### Instance check ids (v1)
- `instance.migrations.meta_keys` — 4 one-shot migration markers present
- `instance.legacy.goal_status_lifecycle` — no NULL/non-`executing` rows
- `instance.legacy.deliveries_ref_id` — no NULL `ref_id`
- `instance.legacy.goal_docs_table` — table absent
- `instance.legacy.inbox_cursor_column` — column absent
- `instance.auth.credentials_file` — presence + `expiresAt` horizon
- `instance.auth.claude_json` — parseable, `oauthAccount` non-empty
- `instance.auth.setup_token` — `CLAUDE_CODE_OAUTH_TOKEN` presence (boolean only)
- `instance.auth.pause` — active auth/usage pause state
- `instance.skills.bundle` — resolver yields files for all 4 known kinds
- `instance.schedule.raw_key` — absent vs corrupt vs valid (global + per-goal)
- `instance.schedule.dispatch` — current gate verdict (informational)

### Project check ids (v1 = US1 subset; +US2/US3 rows)
- `project.workspace.preflight` — `workspace_is_dispatchable` reason
- `project.links.dangling` — `goal_ids` entries resolving to no goal
- `project.links.unstamped_goals` — workspace-matching goals with no `project_id`
- `project.manifest.presence` *(US2)* — absent ⇒ warn (remedy: onboard)
- `project.manifest.valid` *(US2)* — malformed / schema-too-new ⇒ fail
- `project.manifest.revision` *(US3)* — `boilerplate_revision` vs code constant
- `project.markers.integrity` *(US3)* — `devclaw:managed` pairing in AGENTS.md
- `project.scaffold.drift` *(US3)* — `.specify/` vs packaged source

## Project manifest (`devclaw.json`, repo-owned)

### `Manifest` (frozen dataclass in `devclaw/project_manifest.py`)
| field | JSON key | type | default | consumer |
|---|---|---|---|---|
| `schema_version` | `schemaVersion` | int (required) | — | doorway (compat gate) |
| — | `$schema` | str (optional) | — | editors only |
| `boilerplate_revision` | `boilerplateRevision` | int | 0 | doctor US3 |
| `strictness_default` | `strictnessDefault` | `"trust"`\|`"strict"`\| absent | absent | `effective_strictness` resolver |
| `surface` | `surface` | `"app"`\|`"library"`\| absent | absent (= heuristics) | browser gate seams |
| `verify_cmd` | `verifyCmd` | str \| absent | absent | `goal/engine.py` precedence tier |
| `stack` | `stack` | list[str] | `[]` | informational v1 |

Constants: `MANIFEST_NAME = "devclaw.json"`, `SCHEMA_VERSION = 1`,
`BOILERPLATE_REVISION = 1` — all in the doorway module (one home).

### Validation rules (fail-loud, doorway-owned)
- Not valid JSON / not an object ⇒ `ManifestError` ("malformed")
- `schemaVersion` missing or not a positive int ⇒ `ManifestError`
- `schemaVersion > SCHEMA_VERSION` ⇒ `ManifestError` ("instance too old for
  this repo") — distinct message
- Enum fields outside their domain ⇒ `ManifestError` (never coerced)
- Unknown keys ⇒ **allowed** (forward-compat within a schema version)
- Absent file ⇒ `None` from the loader (not an error)

### Read contract
`load_manifest(workspace_dir, ref=None)`:
- `ref=None` → read the worktree file (pre-dispatch reads: preflight,
  verify_cmd tier, strictness resolution)
- `ref="<pre_run_sha>"` → `git show <sha>:devclaw.json` (post-run reads:
  browser-gate surface at settle). Git failure on a post-run read ⇒
  `ManifestError` (not-best-effort posture, like `task_change.py`).

## State-shape changes to existing entities

### `Goal.strictness` — explicitness becomes observable
- `goal.yaml`: `strictness` written **only when explicitly set** (create
  param present, or `set_strictness` called). Existing files with the key
  keep meaning "explicit".
- Loader: absent key ⇒ raw `None` (today: silently coerced to `"trust"`).
  The public `Goal.strictness` keeps its non-null resolved value for
  compatibility; the raw tier is exposed as `Goal.strictness_explicit:
  Optional[Strictness]`.
- New pure resolver: `effective_strictness(explicit, manifest_default) ->
  Strictness` — explicit > manifest > `"trust"`; any unrecognized input
  resolves fail-closed (treated as `"strict"`).
- Task/program rows: unchanged — they snapshot the resolved value at
  dispatch (pre-run ⇒ tamper-safe).

### No other schema changes
No new tables, columns, meta keys, or env vars. Doctor writes nothing.
