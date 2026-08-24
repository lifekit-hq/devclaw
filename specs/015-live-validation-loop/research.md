# Research — spec 015 (live-validation loop)

Grounded in the seam map taken 2026-08-24 against this worktree (file:line
anchors verified). The spec's 15 grilled decisions stand; these are the
planning-level resolutions under them.

## D1 — US1 enforcement points: three existing seams, no new machinery

**Decision**: (a) `.specify/templates/spec-template.md` — the acceptance-
scenario guidance requires each scenario be expressible as an executable test
at the feature's outermost surface; (b) `devclaw/prompts/intake-readiness.md`
— grounding element (c) "verifiable intent" is tightened to "checkable by an
executable test at the feature's outermost surface (browser e2e for UI, HTTP
against the running service for backend, observation of the running scheduler
for jobs) — an outcome only a human walkthrough could check is not ready";
(c) `devclaw/prompts/goal-evaluator.md` — the structural-axis enumeration
gains "spec acceptance scenarios with no covering executable test".

**Rationale**: `validate()` in `intake_readiness.py` and the done-gate's
`structural_concerns` are free-form lists — the host machinery needs zero
change; the criteria live where criteria already live (the prompts), per the
SDLC pipeline rule (judgment → reasoning, standard → prompt).

**FR-002 resolution**: the browser gate ALREADY demands an executed run
(`_executed()` counts expected+unexpected+flaky, skipped excluded; a grep for
intent is explicitly rejected) with the dial semantics the spec asks for.
Nothing changes; a named regression test pins the requirement to this spec.

**Alternatives considered**: a mechanical host-side check that a spec's
scenarios map to test files (rejected — filename↔scenario mapping is
heuristic, exactly what the browser gate's no-grep rule forbids; the
done-gate's grounded reading is the right altitude).

## D2 — Validation contract rides `devclaw.json` (`validation` key)

**Decision**: `devclaw.json` gains an optional nested object:
`"validation": {"boot": "<cmd>", "suites": "<cmd>", "smokePath": "/"}`.
`boot` starts the hermetic seeded instance and exits 0 only when it is up
(seeding and readiness-wait are the script's job — the repo owns its own boot
semantics); `suites` runs the accumulated acceptance suites against it;
`smokePath` (optional, default `/`) is the read-only path the post-deploy prod
smoke GETs. Parsed fail-loud in `parse_manifest`; resolved host-side by
`resolve_validation_contract(workspace_dir)` from the merged base — the
spec-016 trust boundary (worker edits to the manifest can't change what the
host executes... note the commands themselves run IN the sandbox, so the
trust boundary here protects *which* contract is run, and the sandbox is
already the designated place for repo-controlled code).

**Rationale**: `devclaw.json` is the one declared, PR-reviewed, host-read,
versioned per-repo config channel (spec 016); `parse_manifest` tolerates
unknown keys so old instances skip it. First nested structure in the manifest
— accepted, the alternative channels (`.agent/skills/` prose, `AGENTS.md`)
are worker-facing and not machine-parseable.

**Alternatives considered**: a separate `.devclaw/validation.json` (a second
config doorway — violates the one-doorway lesson the manifest just
established); registry-side per-project config (not repo-owned, invisible in
PRs, drifts from the code that defines the boot script).

## D3 — `validate_product` is an agent-less runner branch; findings granularity

**Decision**: the runner handles `kind == "validate_product"` WITHOUT
spawning the ACP agent: run `boot` (bounded), run `suites` (bounded), read
the Playwright JSON report (existing `.devclaw/playwright-report.json`
plumbing) and recursively extract failing test titles, emit a
`validation_report` result field, exit. One finding per failing test title
(fingerprint `validator|<title>`); a suites failure with no parseable report
degrades to ONE run-level finding (fingerprint `validator|suite-exit`)
carrying the output tail — stated, not silent. Boot failure → one finding
(`validator|boot`), most severe. Missing contract → the run fails loud and
the missing contract is itself filed (`validator|missing-contract`).

**Rationale**: zero-LLM-during-execution (FR-005) is strongest when no agent
exists to call one; the Playwright JSON report is the only machine-readable
suite format the system already understands (map §8), and per-title
fingerprints give 014's dedup real scenario identity (the flaky edge case
stays one accumulating issue).

**Alternatives considered**: driving the agent to run the suites (an LLM in
the loop for a mechanical job — forbidden by FR-005 and wasteful); a new
generic suite-report format (build it when a second consumer exists).

## D4 — The HOST files findings; the runner only reports

**Decision**: the runner returns `validation_report` in its result JSON; the
settle path hands it to layer 2 (`goal/validation.py`), which maps failures →
`MachineFinding(source="validator", spec_ref=<scenario/title>)` and calls
`issue_doorway.file_finding` against the project's repo slug.

**Rationale**: the sandbox carries no GitHub credential by invariant (spec
014 assumption, unchanged); the host already owns every gh call.

## D5 — Validation runs never mutate: settle skips materialize and discards the workspace

**Decision**: in the settle path, `validate_product` is excluded from
materialization/change-attachment and from delivery; after the run the
workspace is restored (`git reset --hard` + `git clean -fd`) so boot/seed
artifacts never become commits. No review/browser gate consultation (nothing
to review); verify gate no-ops (`verify_cmd=None` for this kind).

**Rationale**: FR-005 "never creates commits/PRs" must hold even when a boot
script drops artifacts in the tree; spec 013's materialize gate exists to
capture *agent work*, and a validation run performs none by definition — an
explicit kind-level exclusion is honest, a materialized boot artifact would
be a lie about what the agent changed.

## D6 — `GoalMode` gains `"qa"`: the never-self-advancing point on the ADR-0003 dial

**Decision**: `GoalMode = Literal["long_lived", "one_shot", "qa"]`. A `qa`
goal: has a standing `done_when` by construction (creation supplies the
standing contract text); is skipped by the tick's advance path entirely (no
`should_plan`, no done-gate opening on settled validation results — they are
logged as run records instead); receives work only from the deploy trigger,
the (OFF-by-default) cadence, or a manual dispatch. One per project
(`project_id`-keyed lookup; the single-writer project hold does not apply to
qa dispatches — a validation run is read-only toward the repo and must not
block, or be blocked by, feature work... resolved: qa validation tasks ride
a SEPARATE workspace clone (`<workspace>-qa`) so they never contend with the
feature goal's workspace at all).

**Rationale**: FR-007 wants a non-terminating, zero-idle-cognition owner of
validation runs; `is_standing()` + a mode value that never plans delivers
both trivially. ADR 0003's dial is re-evaluation cadence over ONE execution
path — `qa` extends the dial (cadence: never), not the path.

**Alternatives considered**: a boolean `Goal.qa` flag (a mode in disguise —
two fields encoding one concept); no goal at all, trigger→queue directly
(rejected: FR-007 names the QA goal as the owner; without it runs have no
durable home for logs/steering/status and no opt-in handle).

## D7 — Post-deploy trigger: a layer-2 verb, called from both deploy edges

**Decision**: `goal/validation.py::trigger_validation(project_id)` — finds
the project's `qa` goal (none ⇒ no-op: opt-in per repo), resolves the
validation contract, submits ONE `validate_product` task attached to the qa
goal. Called from (a) the `deploy_project` MCP tool after a successful
deploy (layer 1 calls the layer-2 verb — the legal 1→2 chain) and (b) the
auto-deploy edge in `tick_donegate._auto_deploy` (the map's one existing
"deploy completed" seam).

**Rationale**: deploys are owner button-presses (spec 005 FR-008 doctrine),
so this trigger is human-caused by construction — exactly US3's launch
posture. Layer 1 must not dispatch tasks itself; the verb keeps the chain
legal.

## D8 — Prod smoke: host-side read-only GET, findings via the doorway

**Decision**: after a successful deploy, the host GETs the deploy's loopback
URL at the contract's `smokePath` (default `/`) with a short timeout; any
non-2xx/3xx or connection failure files ONE finding
(`source="deploy_smoke"`, fingerprint `deploy_smoke|<slug>|<path>`). Runs
regardless of whether a qa goal exists (it is read-only and free); zero LLM.

**Rationale**: FR-009 — prod gets read-only smoke only; the existing
`_ready()` poll is in-container liveness, not an owner-visible assertion.
One GET is the honest v1; richer smoke belongs to the contract when earned.

## D9 — Periodic schedule ships OFF: the qa goal's `cadence`, empty by default

**Decision**: a qa goal created without an explicit `cadence` has cadence
disarmed (no periodic runs, no cognition, no subprocess on idle ticks). An
owner arms it by recreating the goal with a cadence (goals are durable — no
field patches, per standing rule); armed cadence enqueues a validation run
when due AND inside the run window (existing `dispatch_gate` window math).
Enqueueing is mechanical — the zero-token guard tests stay green either way.

**Rationale**: FR-008 verbatim; reuses `Goal.cadence` + `cadence_due` +
`within_window` instead of inventing a scheduler.

## D10 — PR slicing: US1 alone (off main), US2+US3 together (stacked on 014)

**Decision**: PR-A = US1 (prompts/template/tests — zero dependency on the
doorway, branch `015-acceptance-upstream` off `origin/main`). PR-B = US2+US3
on this branch, stacked on `014-issue-doorway` (#677) because every finding
files through the doorway; retarget to main after #677 merges (git-workflow
stacked-PR procedure).

**Rationale**: US2 without US3 is a mechanism with no trigger — a mid-state
that costs a third stacked PR and reviews worse than the loop as one
coherent unit; US1 is genuinely independent and reviewable alone.
