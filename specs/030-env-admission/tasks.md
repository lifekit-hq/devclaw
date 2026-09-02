# Tasks — spec 030 environment-capability admission

**Status**: IMPLEMENTED — all user stories and FRs done (#800), plus the two
post-landing corrections below.

## US1 — A broken capability holds the project (P1)

- [x] T001 `devclaw/env_cap.py`: probe result type, TTL-cached persistence in
      the StateStore meta table (`env_cap_probe:<id>`), the v1 probe runners
      (`registry:npm-github` reusing doctor's `_probe_registry_token`,
      `sandbox:image` via docker inspect/pull), `run_if_stale` /
      `refresh_needed` / `read_result` / `red_caps_for` (FR-001, FR-006, FR-007)
- [x] T002 `devclaw/project_manifest.py`: the explicit `capabilities` key —
      list of non-empty strings, fail-loud on malformation (FR-005)
- [x] T003 `devclaw/goal/tick_dispatch.py`: the admission read at the dispatch
      boundary — declared capabilities × persisted red results ⇒ hold; zero
      network on this path; `review_repository` exempt (FR-002)
- [x] T004 `devclaw/goal/tick_guards.py::_block_on_env_cap`: `mechanical:env`
      block naming every red probe id + remedy, one owner ping per hold
      episode (FR-002, FR-003)
- [x] T005 `devclaw/goal/store/base.py`: `get_meta` / `set_meta` passthroughs —
      the tick path holds a `GoalStore`, so without them the admission read
      raised `AttributeError` and the gate could never run

## US2 — Auto-resume when the capability heals (P2)

- [x] T006 `devclaw/goal/tick.py::tick_all`: refresh stale probes ONCE per
      sweep, before the per-goal loop, and only for capabilities a registered
      project declares; best-effort, never wedges the sweep (FR-004)
- [x] T007 `devclaw/goal/tick_guards.py::_autoheal_env_cap`: lift the hold when
      no declared capability is red — persisted-row read, zero LLM, no backoff
      window needed; heal budget (`ENV_HEAL_CAP`) parks a FLAPPING capability
      for the owner instead of cycling (FR-003, FR-008 via the existing
      blocked skip-over)

## US3 — The hold is legible everywhere (P3)

- [x] T008 `red_caps_for` returns `(capability id, result)` pairs so the block
      names the same probe id doctor reports — probe evidence alone does not
      carry it
- [x] T009 `docs/reference/devclaw-manifest.md` + `devclaw-manifest.schema.json`
      + `docs/INDEX.md` currency tag: the `capabilities` key, the v1 ids, the
      fail-open posture, and the deliberate worktree-not-merged-base read

## Tripwire tests

- [x] T010 `tests/test_env_cap_admission.py` — the brake's tripwire classes:
      red-probe hold (no dispatch, one ping, `FakeClaude.calls == 0`, held
      ticks stay free and silent), auto-resume with no operator verb and no
      second ping, the fail-open matrix (green / unknown / never-probed /
      undeclared capability / project declaring nothing), flap convergence to
      held + one ping then the gave-up park, and "probes never run on the
      per-goal tick path; once per sweep in `tick_all`, declared ids only"

## FR-005a — doctor advisory

- [x] T011 `devclaw/doctor/checks_project.py::check_capability_declaration`:
      ADVISORY check — repo visibly depends on a private registry (`.npmrc` /
      `package-lock.json` resolving against `npm.pkg.github.com`) but
      `devclaw.json` declares no `registry:*` capability. WARN + the manifest
      fix as remedy; never a FAIL, never a hold. Bounded head reads, no network
- [x] T012 `tests/test_doctor.py`: seeded-fault test for T011 (the
      doctor-check rule in CLAUDE.md) — WARN naming the file and the remedy,
      settling to OK once the capability is declared — plus the clean-repo case

## Polish

- [x] T013 Full suite + `ruff check .` + `mypy` green before the PR

## Post-landing corrections (done-gate findings on #800, 2026-09-02)

- [x] T014 US3 was only half-met: the goal's block named `registry:npm-github`
      while doctor's `check_registry_token` named nothing but its own check id,
      so the operator still read TWO stories. `devclaw/env_cap.py` now owns the
      capability-id constants (`CAP_REGISTRY_NPM_GITHUB` / `CAP_SANDBOX_IMAGE`)
      AND the registry probe primitive + token-shape rule that
      `devclaw/doctor/checks_instance.py` used to own; doctor imports both and
      names the capability id in every non-OK remedy. Also reverses the
      core→doctor import env_cap shipped with.
      `devclaw/doctor/checks_project.py` drops its re-typed literal.
- [x] T015 FR-003's "exactly one owner ping per hold episode" leaked in the
      other direction: `_block_on_env_cap` gated the ping on
      `heal_attempts == 0`, a counter shared with every `mechanical:*` heal, so
      a goal with any prior heal got NO ping on its first env breakage. New
      `goal_status.env_hold_notified` column (the `no_progress_notified`
      shape) — set mark-first at ping time, reset on a productive settle
      (`devclaw/goal/tick_settle.py`) and on `steer_goal` / `resume_goal`
      (`devclaw/goal/service.py`).
- [x] T016 Persisted-state-shape change ⇒ its doctor check (CLAUDE.md /
      spec-016 FR-014): `instance.env.goal_status_env_hold_notified`. The
      per-column check body was duplicated per column, so it is extracted to
      `_goal_status_column_finding` and both callers share it; the seeded-fault
      test in `tests/test_doctor.py` is parametrized over the column set rather
      than cloned (never mint an instance-test).
- [x] T017 `tests/test_env_cap_admission.py`: a `heal_attempts > 0` seed case
      proving the ping still fires, and the US3 cross-reference test asserting
      doctor and the goal block name the SAME capability id. The flap test's
      fake settle now carries the damping counters forward instead of
      rebuilding a bare `GoalStatus` — it was resetting the very markers it
      asserted on.
- [x] T018 The TTL was hardcoded at 16 min — WIDER than the 15-min default
      heartbeat, so a red row was still fresh on the sweep after the fix and
      FR-004's "resume within ~one sweep" quietly cost two.
      `devclaw/env_cap.py::probe_ttl_s()` derives it from
      `config.goal_tick_seconds()` (half the cadence) so it tracks
      `DEVCLAW_GOAL_TICK_SECONDS` instead of being invalidated by it. The US2
      resume test now runs two real `tick_all` sweeps with the cached row aged
      between them, exercising the expiry rather than writing the green row by
      hand (which asserted the heal while skipping what delivers it).
- [x] T019 The per-sweep capability scan read EVERY goal, so a cancelled
      goal's stale workspace kept buying the fleet a recurring network/docker
      probe forever. `devclaw/goal/tick.py` scopes the scan with
      `project_hold.is_terminal` — the same rule the hold derivation uses.
      Blocked goals still count on purpose: the env hold IS a block, and
      dropping it would starve the probe refresh its auto-resume needs. The
      once-per-sweep tripwire gains a cancelled-goal case (class test extended,
      not cloned).
