# Tasks — spec 030 environment-capability admission

**Status**: IMPLEMENTED — all user stories and FRs done, PR pending.

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
