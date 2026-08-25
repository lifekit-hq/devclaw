# Research: Scorecard Measures the Ratchet

All decisions grounded in the 2026-08-25 live audit (session evidence: 36
task rows → 18 distinct PRs; 11/13 decided PRs merged vs reported
merge_rate 0.50; per-goal first-pass 5/11 ≈ 0.45 vs reported 0.36) and in
the current code.

## D1 — Where PR ground truth is read

- **Decision**: Persisted `pr_ledger` refreshed OUTSIDE the read path, owned
  by the once-per-cycle report emission (`GoalService._maybe_emit_cycle_report`,
  `devclaw/goal/service.py:457`), which already runs exactly once per cycle
  at window close and already writes through the store single-writer
  (`record_cycle_report` precedent). Scorecard read stays pure SQL; output
  stamps the ledger `as_of`.
- **Rationale**: Clarified with Denys 2026-08-25 (option B). Keeps
  `get_scorecard_metrics` instant, credential-free and failure-isolated;
  merges happen on human time so day-stale + a stamp is honest.
- **Alternatives considered**: live poll at compute time (rejected: network +
  gh dependence inside a read surface); lazy TTL hybrid (rejected: more
  moving parts than the staleness stamp justifies).

## D2 — How the per-goal round count survives the close

- **Decision**: A dedicated `goal_convergence` ledger row written at every
  terminal goal transition — done (`tick_donegate.py` close path, BEFORE the
  status reset) and cancel (the `service.py` cancel sites that currently
  zero `donegate_rounds`). Row: goal_id, outcome (achieved|abandoned),
  rounds, closed_at_ms, project attribution.
- **Rationale**: Follows the `eval_outcomes` precedent exactly — a settle
  ledger written once at the terminal event, directly SQL-aggregable by the
  scorecard. Leaves `goal_status.donegate_rounds` semantics (the live brake
  counter) completely untouched, so the churn-brake logic and its tests are
  unaffected.
- **Alternatives considered**: stop zeroing `donegate_rounds` at close
  (rejected: overloads a live brake counter as historical record, couples
  two meanings to one field, and needs a `goal_phase_history` join for close
  time); append to the state_store `events` log and project at read time
  (rejected: workable but every read re-scans and re-parses; the dedicated
  ledger matches the existing pattern and keeps the metric query trivial).
- **Pre-feature closes**: goals closed before the ledger exists have no row
  → reported in the `rounds_unknown` bucket (US1 scenario 4), never guessed.

## D3 — Platform lookup seam

- **Decision**: Extend `devclaw/goal/remote_checks.py` with a
  `pr_state(pr_url)`-shaped helper on its existing `_gh` async-subprocess
  seam (`gh pr view <url> --json state,mergedAt`), injected into the
  cycle-report step the same way `default_checker()` is bound today so the
  stubbed suite never spawns a process.
- **Rationale**: One GitHub-subprocess home already exists with the right
  test seam and the right layer (bound by goal_service, kept out of tick
  units); delivery's `_run("gh", ...)` calls are workspace-cwd-coupled and
  the wrong shape.
- **Alternatives considered**: HTTP GitHub API client (rejected: new
  credential/config surface; `gh` is already the repo's authenticated
  doorway); inferring merge from local git reachability (rejected in spec —
  re-derives ground truth the platform owns).
- **Bounds**: refresh looks up only PRs still undecided in the ledger among
  those opened within the ratchet window (+ a hard cap, default 50, with a
  loud `refresh_truncated` note when hit — Principle VI).

## D4 — Threshold configuration home

- **Decision**: `devclaw/config.py` (the single DEVCLAW_* doorway):
  `DEVCLAW_RATCHET_FIRST_PASS` (default 0.70),
  `DEVCLAW_RATCHET_DECIDED_MERGE` (default 0.80),
  `DEVCLAW_RATCHET_WINDOW_DAYS` (default 14). Echoed in scorecard output;
  wedge-free condition read from existing `cycle_reports.clean`.
- **Rationale**: the repo's one-home-one-default-one-parse rule for env
  config; thresholds are operator-tunable without a code change (FR-008).
- **Alternatives considered**: a config file / DB row (rejected: no
  precedent, heavier than needed; env is how every other knob works here).

## D5 — Bench/evidence project marking

- **Decision**: `Project.bench: bool = False` on the registry dataclass,
  settable via the existing `update_project` surface, following the
  per-project override pattern already carrying `autodeploy`/`review_gate`/
  etc. Attribution: PRs and goals map to projects via the existing
  workspace-dir normalization (`telemetry._ws_norm` ↔ registry), same as
  `compute_instance_usage`.
- **Rationale**: registration is the operator-owned per-repo decision
  surface; name-pattern matching was rejected in the spec's assumptions.

## D6 — Legacy field disposition (FR-011)

- **Decision**: REMOVE `merge_rate`, `first_pass_hit_rate`, `steer_rate`
  from the output; introduce a `pr` block, a `convergence` block,
  `human_steers`, and a `ratchet` (thresholds + pass/fail) block. Update
  `format_scorecard` (CLI) and the MCP tool docstring in the same increment.
  `estimate_notes` drops the two notes this feature obsoletes and keeps the
  usage-envelope note (FR-012).
- **Rationale**: a field keeping its name must keep its meaning; these three
  can't keep their meaning, so they can't keep their names. Known consumers
  are all in-repo (CLI, MCP tool docstring, console if it renders the tool
  output); the dashboard consumes `/usage.json`, untouched.

## D7 — Human-steer counting

- **Decision**: Count `goal_steering` rows in-window whose `source` does not
  match `auto-%` (today: everything except `auto-eval` — operator rows use
  `denys`/tool-default sources). Read directly by telemetry over the same
  `devclaw.db` connection (goal tables share the file since Tranche 1);
  read-only, so single-writer is untouched.
- **Note**: this read intentionally shares the machine/human source
  distinction with the churn-brake fix
  (`specs/tiny/donegate-churn-brake-holds.md`) — same class rule, two
  consumers.
