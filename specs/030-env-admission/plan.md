# Plan — spec 030 environment-capability admission

**Status**: IMPLEMENTED — all three user stories plus FR-005a.

## Shape

One new module plus four wires — no new layer, no new store.

- `devclaw/env_cap.py` (**new**, layer-agnostic mechanism): the probe registry,
  the TTL-cached persisted result, and the two read helpers. `run_if_stale` /
  `refresh_needed` are the ONLY places that touch a network; `read_result` /
  `red_caps_for` read persisted meta rows and are what the tick path calls.
- `devclaw/project_manifest.py`: the `capabilities` key (FR-005) — a list of
  non-empty strings whose VALUES are checked against `env_cap.KNOWN_CAPABILITIES`
  (the `strictnessDefault`/`surface` shape, not the tolerant `stack` one).
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
- **One ping per hold episode spans a flap**, marked by the brake's OWN
  `goal_status.env_hold_notified` column. A re-block inside the episode logs
  but does not ping, and the heal budget parks the goal at the cap — so a probe
  oscillating green↔red converges to held + one ping (spec edge case) instead
  of a ping storm. The episode ends on a productive settle or when a human
  vouches (`steer_goal` / `resume_goal`), so a genuine later breakage pings
  again. *Revised 2026-09-02*: the first cut gated on `heal_attempts == 0`,
  which is shared with every other `mechanical:*` heal — a goal that had
  earlier healed a `mechanical:prep` block was silently denied the FIRST ping
  of an unrelated environment breakage, i.e. exactly the ping SC-002 promises.
  Precedent for the dedicated flag: `no_progress_notified`.
  *Revised again 2026-09-02*: the heal BUDGET had the same defect and needed
  the same fix — `_autoheal_env_cap` counted on the shared `heal_attempts`,
  so prior prep heals could arrive with the env budget already spent and the
  goal parked instead of auto-resuming within one sweep of the probe greening
  (US2). Each brake now spends its own column (`env_heal_attempts`), and
  `_heal_give_up` takes the counter it parks.
- **Capabilities carry a SCOPE** (`env_cap.CAP_SCOPES`), and the scope is what
  the cache key keys on. `registry:npm-github` is instance-scoped — one
  process-wide env var, so N declaring projects buy one probe. `sandbox:image`
  is project-scoped: a project may pin its own `sandbox_image` (ADR 0005), so
  a fleet-wide row would answer about an image that project never launches, in
  both directions (admitted because the *default* is pullable; held because it
  isn't). The sweep resolves the per-project subject
  (`GoalService._registered_sandbox_images`) and hands it to the probe on
  `CapTarget`; `env_cap` never reaches into the project registry, and the
  per-goal read path needs only the key (`cap_id` + `project_id`), never the
  subject. Rejected: restricting `sandbox:image` to instance scope and ignoring
  it on a pinning project — that removes the check from precisely the projects
  whose image is most likely to be missing.
- **One capability id, imported everywhere** (`env_cap.CAP_REGISTRY_NPM_GITHUB`
  / `CAP_SANDBOX_IMAGE`). The registry probe primitive and the token-shape rule
  moved from `devclaw/doctor/checks_instance.py` into `devclaw/env_cap.py`, and
  doctor imports them — reversing a core→doctor import and giving the id and
  its credential rule ONE home. Doctor's check id stays
  `instance.registry.token` (its own dotted namespace, which operators filter
  on); what US3 requires is that both surfaces NAME the same probe id, so the
  check's non-OK remedy carries the capability id.
- **The parse NORMALIZES capability ids (strips) before storing them**, because
  FR-006's validation compares the stripped id. Any divergence between the form
  validated and the form stored reopens the exact hole FR-006 closes: a padded
  id validates, then keys nothing in `_PROBE_RUNNERS`, probes `unknown`, and
  FR-007 declines to hold on unknown — the brake silently off on a repo that
  reads as protected.
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
- **The probe TTL is DERIVED from the heartbeat cadence, never a constant**
  (`env_cap.probe_ttl_s()` = half `config.goal_tick_seconds()`). The TTL's only
  job is to make a result last exactly one sweep, so it has to move when the
  cadence does. The 16-min literal it replaced was *wider* than the 15-min
  default sweep, which kept a red row fresh through the sweep after the fix —
  FR-004's "resume within ~one sweep" silently cost two, and tightening
  `DEVCLAW_GOAL_TICK_SECONDS` widened that gap instead of closing it.
- **The capability declaration is sourced from the PROJECT REGISTRY**, once per
  sweep (`GoalService._registered_capabilities` → `tick_all` → `TickContext`,
  the `holders` threading precedent), and the resulting `project_id -> caps`
  map is what BOTH the dispatch guard and the auto-heal resolve through
  (`tick_guards.declared_caps_for`). Reading only each goal's own prepared
  workspace — the first implementation — meant a goal whose workspace has never
  been prepared declared nothing, so its FIRST dispatch sailed through a
  capability that was already red on record: precisely the session SC-002
  promises not to burn. Capabilities belong to the project, not the goal, and
  the registry answers before any goal workspace exists. Splitting the
  resolution between the two consumers is what makes an unprepared-workspace
  hold clear itself every tick, so there is exactly ONE resolver.
- **Live goals' workspaces are still scanned on top**, for goals belonging to
  no registered project, and scoped to non-terminal goals there
  (`project_hold.is_terminal`, the same rule the hold derivation uses). A done
  or cancelled goal's workspace can never be dispatched into again, so its
  declaration must not buy the fleet a recurring network/docker probe forever.
  Blocked goals deliberately still count: the env hold IS a block, and dropping
  it from the scan would starve the very probe refresh its auto-resume needs.
  Archived projects are skipped for the same reason.
- **The per-project map is authoritative only where it ANSWERED.** A project is
  recorded only when its manifest was actually read; an absent checkout, an
  absent `devclaw.json` and an unreadable one are all "no answer" and the
  project is OMITTED, so its goals fall back to the workspace read. *Corrected
  2026-09-02*: the first cut recorded `()` whenever `load_manifest` returned
  `None` — which is every not-yet-cloned checkout — and because a present-but-
  empty entry deliberately SUPPRESSES the fallback, that turned "could not
  read" into a licence to dispatch and repealed the brake for exactly the goals
  whose own workspace carried the declaration. Only a raise was omitted, which
  is the rarer half of "cannot be read".
- **A DECLARED capability's missing credential is red, not a posture.**
  `registry:npm-github` with no `NODE_AUTH_TOKEN` probes red: the probe runs
  only because a project declared the capability, and an install against
  GitHub Packages 401s deterministically without a credential — evidence of
  breakage (fail-closed), not the uncertainty FR-007 fails open on. Doctor's
  `instance.registry.token` is instance-scoped and has no declaration to read,
  so it keeps unset-is-OK *until* some registered project declares the
  capability, at which point it reports the same fault (US3: one story).
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
