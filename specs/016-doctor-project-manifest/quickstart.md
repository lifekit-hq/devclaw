# Quickstart validation: spec 016 (doctor + manifest)

Runnable proof scenarios per user story. Prereqs: `pip install -e ".[dev]"`;
suite conventions per `.claude/rules/testing.md` (private TMPDIR, `-n auto`).

## US1 — instance doctor

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_doctor.py
```

Expected: seeded-fault tests pass — one test per drift class
(missing migration meta key, legacy lifecycle row, NULL delivery ref_id,
absent credentials file, expired `expiresAt`, missing skills bundle dir,
absent raw `run_schedule` key, dangling `goal_ids` link, unstamped goal,
undispatchable workspace), plus:

- `test_doctor_spends_zero_tokens_and_writes_nothing` — `FakeClaude.calls == 0`
  and byte-identical DB before/after.
- `test_doctor_reports_healthy_affirmatively` — all-ok state lists every
  check as `ok`.
- `test_doctor_is_deterministic` — two runs, identical JSON.

Live smoke (dev host, no server needed):

```bash
.venv/bin/python -m devclaw.cli doctor --json | python -m json.tool
```

Expected: valid report JSON; exit 0/1 per contract.

## US2 — manifest doorway + consumption

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_project_manifest.py tests/test_manifest_gates.py
```

Expected:

- parse: valid manifest round-trips; malformed ⇒ `ManifestError`; newer
  `schemaVersion` ⇒ the distinct "instance too old" error; absent file ⇒ `None`.
- dispatch: malformed manifest rejects `dispatch_task`/goal dispatch loudly
  (message names the file + parse error); absent manifest dispatches on
  defaults.
- strictness: goal without explicit strictness + manifest `strict` ⇒ review
  gate consulted; explicit per-goal `trust` beats manifest `strict`
  (most-specific-wins); no manifest ⇒ today's behavior byte-identical.
- verify_cmd: `action > goal > manifest` tier order asserted at the engine
  seam.
- **pre_run_sha pinning (the FR-009 named regression)**:
  `test_manifest_edit_inside_run_does_not_change_gate_inputs` — commit
  manifest `surface: app`, capture pre_run_sha, commit `surface: library`
  as the "worker", settle ⇒ browser gate still evaluates app-surface.
- surface: `surface: library` repo with a frontend-touching diff ⇒ library
  exemption without glob heuristics.

## US3 — drift + migration

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_doctor.py -k "revision or marker or scaffold"
```

Expected: bumped `BOILERPLATE_REVISION` constant vs fixture manifest ⇒
`project.manifest.revision` finding naming both revisions + `onboard` remedy;
unpaired `devclaw:managed` marker ⇒ integrity finding; mutated `.specify/`
scaffold file ⇒ drift finding.

## Full gate before each PR

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q   # ≥ current baseline, no losses
ruff check .
mypy
```

Structural guards that must stay green: `test_config_single_doorway.py`
(no new env reads outside config.py), `test_views_never_read_back.py`
(doctor reads SQLite, not STATUS.md), `test_env_vars_doc_sync.py` (no new
env vars introduced), zero-token guards across `tests/test_goal_tick.py`.
