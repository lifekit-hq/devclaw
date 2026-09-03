# Tasks — spec 030 environment-capability admission

**Status**: COMPLETE — all user stories and FRs done (#800), plus the
post-landing correction rounds below. FR-004's one remaining deviation (the
live-goal workspace fallback probes for capabilities no *registered* project
declares) was ruled accepted by Denys on 2026-09-03 and is recorded in
spec.md; nothing in this spec is outstanding.

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
      sweep, before the per-goal loop, and only for capabilities some project
      declares — the registry first, then the live-goal workspace fallback for
      projects the registry cannot answer for (see spec.md's accepted FR-004
      deviation); best-effort, never wedges the sweep (FR-004)
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
- [x] T020 The scan read LIVE GOALS' prepared workspaces, so a goal whose
      workspace has never been prepared declared nothing and its FIRST-EVER
      dispatch ran unguarded against a capability already red on record — the
      hole in SC-002's "zero worker sessions until rotated". The declaration is
      now sourced from the PROJECT REGISTRY once per sweep
      (`GoalService._registered_capabilities`, threaded through `tick_all` →
      `TickContext.project_caps` on the `holders` precedent), and both
      consumers — the dispatch guard and the `mechanical:env` auto-heal —
      resolve it through the single `tick_guards.declared_caps_for`; splitting
      that resolution is what would make an unprepared-workspace hold clear
      itself straight back into the red capability every tick. Still
      once-per-sweep and zero-LLM. The US1 class test is parametrized over the
      declaration SOURCE (workspace / registry-on-unprepared-workspace) and the
      once-per-sweep test gains a registry-declared probe case — both extended,
      neither cloned. (Its "declares nothing is authoritative" half was only
      half-built — see T023.)

## Post-landing corrections (done-gate findings, round 2, 2026-09-02)

- [x] T021 A declared `registry:npm-github` with NO credential probed GREEN —
      the deterministic `npm ci` 401 burn SC-002 exists to prevent, admitted as
      a "supported posture". The probe runs only because a project DECLARED the
      capability, so the absence is a missing dependency:
      `devclaw/env_cap.py` returns red with a shared `REGISTRY_UNSET_REMEDY`.
      `devclaw/doctor/checks_instance.py` keeps unset-is-OK only while no
      registered project declares the capability
      (`_projects_declaring_registry_cap`), so doctor cannot answer OK on the
      state a goal is being held by (US3).
- [x] T022 `KNOWN_CAPABILITIES` was a dead constant — nothing consulted it, so
      a typo'd id (`registry:npmgithub`) read as protection in the repo while
      producing zero probing and zero holding.
      `devclaw/project_manifest.py::parse_manifest` now value-validates
      capability ids against it and fails loud — the `strictnessDefault` /
      `surface` precedent, not the tolerant `stack` one. The manifest schema's
      `capabilities` items gain the matching `enum`.
- [x] T023 T020's claim ran ahead of its code:
      `GoalService._registered_capabilities` omitted a project only when
      `load_manifest` RAISED, and recorded `()` when it returned `None` — every
      not-yet-cloned checkout and every repo without a `devclaw.json`. Since a
      present-but-empty entry deliberately suppresses the goal-workspace
      fallback, that failed a red capability OPEN. A project is now recorded
      only when its manifest was actually READ; the US1 class test gains a
      third declaration source (`project_registry_no_checkout`) that runs the
      REAL resolver and proves the hold still fires. plan.md's bullet is
      corrected to describe the code rather than the intent.
- [x] T024 `docs/reference/devclaw-manifest.md`, the manifest schema,
      `docs/reference/env-vars.md` (`NODE_AUTH_TOKEN` unset is no longer
      byte-identical for a declaring project) and both `docs/INDEX.md` currency
      tags.

## Post-landing corrections (done-gate findings, round 3, 2026-09-02)

- [x] T025 `sandbox:image` answered about the WRONG image for any project
      pinning its own (`projects.sandbox_image`, ADR 0005): the probe read the
      fleet-default `SANDBOX_IMAGE` and cached one row keyed by capability id
      alone. Capabilities now carry a scope (`env_cap.CAP_SCOPES`) and the
      scope is what `_meta_key` keys on — instance-scoped ids keep one shared
      row, `sandbox:image` gets one per project. The sweep resolves the
      subject (`GoalService._registered_sandbox_images` → `tick_all`'s
      `project_images` → `CapTarget.subject`) so `env_cap` stays ignorant of
      the project registry; both readers (`red_caps_for` at the dispatch guard
      and in the auto-heal) pass the goal's `project_id`. The once-per-sweep
      class test gains the scope case: two projects, distinct subjects,
      per-project rows, and one shared row for the instance-scoped id.
- [x] T026 The env auto-heal spent the SHARED `heal_attempts`, so a goal with
      prior `mechanical:prep` heals could reach its first env hold with the
      budget gone and stay parked instead of auto-resuming within one sweep of
      the probe greening (US2) — the same defect T015 fixed for the ping, in
      the other counter. New `goal_status.env_heal_attempts` column (the
      `env_hold_notified` shape, reset on the same three events);
      `_heal_give_up` takes the `counter_field` it parks and `_heal_unblock`
      writes only the budget its caller spent. Persisted-state shape ⇒ its
      doctor check (`instance.env.goal_status_env_heal_attempts`), a case on
      the parametrized `_BRAKE_COLUMNS` seeded-fault test. T015's class test is
      parametrized over the prior-heal count (nonzero / past `ENV_HEAL_CAP`)
      and now asserts the resume too — never a sibling test.

## Close-out (2026-09-03)

- [x] T027 The env auto-heal resolved the declaration with a blocking
      `load_manifest` straight on the heartbeat's event loop, while its sibling
      call site (`tick_dispatch.py`) already ran the same resolver through
      `asyncio.to_thread`. One resolver, one calling convention — both are now
      off-loop, and `_declared_caps_for`'s docstring states the constraint so
      the next caller does not have to rediscover it.
- [x] T028 `devclaw/goal/tick.py`'s mechanical-auto-heal comment still read
      "One healable kind: `prep`" after spec 030 added `env` — the second
      healable kind, with a deliberately different recheck shape (persisted-row
      read, hence no backoff window). Corrected in place.
- [x] T029 FR-004's accepted deviation recorded in `spec.md` as direction
      memory (owner ruling 2026-09-03: accept the gap and close), so the
      registry-only reading is not re-litigated into a fail-open regression.
