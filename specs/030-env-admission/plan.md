# Plan — spec 030 environment-capability admission

**Status**: IMPLEMENTED — all three user stories plus FR-005a.

## Shape

One new module plus four wires — no new layer, no new store.

- `devclaw/env_cap.py` (**new**, layer-agnostic mechanism): the probe registry,
  the TTL-cached persisted result, and the two read helpers. `run_if_stale` /
  `refresh_needed` are the ONLY places that touch a network; `read_result` /
  `red_caps_for` read persisted meta rows and are what the tick path calls.
- `devclaw/project_manifest.py`: the `capabilities` key (FR-005) — a list of
  non-empty strings, validated in the same fail-loud shape as `stack`.
- `devclaw/goal/tick.py::tick_all`: the once-per-sweep probe refresh, in the
  pre-loop housekeeping slot beside `_project_hold.holder_map` (the existing
  precedent for "computed once per sweep, threaded into each tick").
- `devclaw/goal/tick_dispatch.py::_dispatch_action`: the admission read, beside
  the other dispatch-boundary holds; `review_repository` is exempt (a read-only
  review examines the repo, it never runs it).
- `devclaw/goal/tick_guards.py`: `_block_on_env_cap` / `_autoheal_env_cap`,
  modelled on `_block_on_prep_failure` / `_autoheal_prep`.

## Load-bearing choices (do not relitigate)

- **Probe results live in the StateStore meta table**, keyed
  `env_cap_probe:<cap id>`, carrying `status` + `evidence` + `remedy` +
  `probed_at_ms`. Precedent: `run_schedule` / `pr_ledger_refresh` (doctor) and
  the workspace-sweep stamp in `goal/engine.py`. No new table for two rows.
- **`GoalStore.get_meta` / `set_meta` are thin passthroughs** to the shared
  StateStore, in the same shape as `mark_self_deploy_pending` — the goal layer
  must not reach into `_state`. Without them the admission read raises
  `AttributeError`: the tick path holds a `GoalStore`, not a `StateStore`.
- **No backoff on the env heal.** `_autoheal_prep` needs a backoff window
  because its recheck costs a git subprocess; the env recheck IS a persisted-row
  read, so a blocked tick stays zero-subprocess without one. The heal BUDGET is
  still kept (`ENV_HEAL_CAP`), for the flap case only.
- **One ping per hold episode spans a flap.** A re-block after an env heal
  (`heal_attempts > 0`) logs but does not ping, and the budget parks the goal
  at the cap — so a probe oscillating green↔red converges to held + one ping
  (spec edge case) instead of a ping storm. A productive settle resets
  `heal_attempts`, so a genuine later breakage pings again.
- **`capabilities` is read from the WORKTREE, not the merged base** — a
  deliberate divergence from the `devclaw.json` gate trust boundary
  (`docs/reference/devclaw-manifest.md`). The hold is a session-burn brake, not
  a quality gate: a worker-side edit can only hurt that worker's own goal, never
  bias a gate toward shipping. The merged-base read would also put a git
  subprocess on every blocked tick, which the zero-subprocess blocked-tick
  convention forbids.
- **The block message carries the capability id explicitly** (`red_caps_for`
  returns `(cap_id, result)` pairs): US3 requires the goal's block and doctor's
  finding to name the SAME probe id, and probe evidence alone does not carry it.
- **`mechanical:env` is not `mechanical:env_cap`.** The latter already exists
  (spec 020: the sandbox-OOM re-dispatch budget). Different brake, different
  kind string — do not merge them.

## Slice surfaces (the next session's read budget)

- **US1 + US2 + US3 (landed)**: `devclaw/env_cap.py`,
  `devclaw/project_manifest.py`, `devclaw/goal/tick.py`,
  `devclaw/goal/tick_dispatch.py`, `devclaw/goal/tick_guards.py`,
  `devclaw/goal/store/base.py`, `tests/test_env_cap_admission.py`,
  `docs/reference/devclaw-manifest.{md,schema.json}`, `docs/INDEX.md`.
  Constraint: everything on the tick path reads persisted rows only.
- **FR-005a (landed)**: `devclaw/doctor/checks_project.py` (+ `PROJECT_CHECKS`)
  and `tests/test_doctor.py`. An ADVISORY project check: a repo whose npm
  config/lockfile visibly resolves against a private registry while
  `devclaw.json` declares no `registry:*` capability. Constraint: WARN only —
  it must never become a FAIL or a hold, or explicit-only declaration
  (FR-005) has been quietly repealed.

## Out of scope (recorded so it is not re-argued)

Claude auth as a capability (owned by the usage/auth pause brake), a
docker-daemon probe (already fails loudly), capability derivation/inference
(FR-005 is explicit-only), and verify-gate-side enforcement (the spec's
rejected alternatives).
