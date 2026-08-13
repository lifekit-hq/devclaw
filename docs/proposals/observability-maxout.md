# Observability max-out — watch the watcher, remember the metrics, schedule the evals

**Status: LOCKED (direction)** — 2026-08-12. Drafted and clarified the same
session; all 5 `[OPEN]` items resolved with Denys (answers inline in §Resolved
below). Locking commits direction, not schedule — tranche sequencing stays
Denys's call. P1 firmed and sized; P2/P3 named, P2 sized after P1.

## Origin

A 2026-08-12 grounded audit of monitoring/observability/evaluation (repo sweep
+ live-instance readout), triggered by Denys: *"I want these things maxed out —
production level grade."* The audit's verdict: the instrumentation **inside**
the loop is strong (trace net + transcripts, deduped problems catalog, live
`eval_outcomes` projection, clean-cycle self-grading, retention hygiene); what
is missing is everything **around** it. The system measures itself honestly,
but nothing acts on the measurements — and nothing notices if the measurer
dies.

**The bar.** Production grade for a single-operator autonomous system is not
enterprise SRE tooling. It is two properties:

1. The operator learns about every failure from a **ping, never from
   silence** — including the failure of devclaw itself.
2. Every diagnosis is answerable **through the MCP/HTTP surface** — never
   ssh + docker inspect + raw sqlite (the 2026-08-12 ledger night-1 hunt
   needed all three; issues #494–#496 are the receipts).

## Grounded gaps (audit summary)

1. **Nothing watches the daemon.** No heartbeat-freshness signal, no dead-man
   on the nightly cycle report. `/health` returns `ok` as long as the process
   serves HTTP — the heartbeat coroutine can be silently dead behind it. An
   absent 2am cycle report alerts no one.
2. **Metrics have no memory.** `compute_scorecard` is snapshot-only; nothing
   persists history, nothing can chart merge-rate across the watch nights,
   nothing fires on degradation.
3. **Alerting is binary, single-channel, best-effort.** OWNER/TASK only;
   goal-layer notify is 2xx-or-drop with no retry; each ping site reinvents
   its own dedup; self-triage is allowlisted to exactly `db_size`.
4. **Evals are never scheduled.** CI is stubbed pytest only (hosted runners
   billing-locked); the sandbox E2E suite, eval-judge, and compounding
   scorecard are all operator-run.
5. **Diagnosis surfaces are thin or lie.** #494 (no deployed SHA), #495
   (delivery strategy invisible), #496 (NULL lifecycle coalesced to
   `"executing"` on read). L3/L5 health is `unknown` on the console strip.

## Ops-agent: grounded verdict (P1 hinges on this)

`lifekit-stack/ops-agent` is a resident sibling process — deployed on the VPS
(compose service, docker socket, `:ro` mounts on devclaw substrates) — built
across ops-PR1–PR6 as detector → playbook (`claude --print`) → action, with a
deliberate one-tool-at-a-time MCP authority boundary. The **separate-process
principle is exactly right and is the only credible home for watching the
watcher**: an in-process self-heal can share the defect that broke devclaw
(the closeloop incident that motivated it).

The **current detector set is not earning its keep**, on live evidence
(incidents dir + container log, 2026-08-12):

- **Zombie incidents daily.** O1 fires every day on `self-fix-issue-393` and
  `finance-sentry-ui-library-v2` — goals cancelled weeks ago — burning one
  cognition call each to decide `noop`. The detectors read on-disk `STATUS.md`
  views for every goal dir ever created and do not filter terminal phases.
- **Run-window blindness.** O3 (verifying-stall) fired on the live ledger
  goal while its only task sat in the 22:00–05:00 dispatch hold — a
  false-positive class: "held" is not "stalled". (Its cognition step then
  failed `non_zero_exit` → noop, so the one relevant detection did nothing.)
- **Blind to the one thing only it could see.** Every detector consumes
  signals devclaw itself writes (`no_progress_notified` frontmatter, verdict
  shapes, trend files). A dead devclaw writes nothing; ops-agent observes
  calm. The unique value of the sibling position — noticing devclaw's own
  death — is exactly what it does not do.
- **Progressive obsolescence of the goal-level legs.** Devclaw now pings the
  owner directly for no-progress/blocks (tick guards), files recurring
  problems as issues itself (self-issue loop — whose proposal explicitly
  "rescues the dead O4 detector"), and the earlier
  `ops-agent-problems-consolidation` proposal was ABANDONED. O1/O2/O4
  duplicate notifications devclaw already sends.

**Verdict: keep the process, repurpose it.** Ops-agent's justified role is the
**mechanical external watchdog + restart authority** — the things that must
work when devclaw's own cognition and notify paths are down, which is
precisely when an LLM-playbook loop is least trustworthy. Clarify outcome on
the goal-level detectors: **hygiene now, deletion decided after the ledger
watch** (Denys, 2026-08-12 — see §Resolved O2), so the watch itself supplies
the evidence on whether O1–O4 ever catch something devclaw's own pings miss.

## P1 — watch the watcher + surface honesty (firmed, ~5 PRs, end-of-week cap)

Sliced so each PR is independently shippable; devclaw-side and ops-agent-side
land in their own repos.

1. **devclaw: heartbeat freshness + build identity on the health surface.**
   `/health` and `/node.json` gain `last_tick_at`, `last_cycle_report_at`, and
   the deployed `git_sha` + `built_at` (#494 — bake at image build; the deploy
   script already computes the SHA). Zero new cognition; reads existing state.
2. **devclaw: goal diagnosis surfaces stop hiding/lying.** `get_goal` /
   `list_goals` / console gain `delivery_strategy` + resolved `goal_branch`
   (#495) and return the raw stored lifecycle — NULL as `null`/`"legacy"`,
   never coalesced (#496).
3. **ops-agent: O5 daemon-liveness detector — zero-LLM, mechanical.** Polls
   `/health`: (a) unreachable/process down, (b) `last_tick_at` staler than
   N×tick-interval, (c) no cycle report by window-close + grace. Actions are
   mechanical (no playbook): owner ping via the notify relay — **ping-only in
   this tranche** (Resolved O1); auto-restart is a named follow-up unlocked
   by correct detections. This is the dead-man's switch.
4. **ops-agent: detector hygiene.** Skip goals in terminal phases
   (cancelled/done) — kills the daily zombie incidents and their cognition
   burn; suppress O1/O3 while dispatch is held (run-window/pause/operator
   hold) — "held" is not "stalled".
5. **devclaw: L3/L5 honesty on the console strip** — derive cognition/worker
   health from the trace tail (last successful cognition call / last worker
   event) instead of hardcoded `unknown`. Display-only.

Invariants check: O5 and the snapshot writes are zero-LLM (idle guard
untouched); ops-agent keeps reading from outside (no devclaw imports, MCP-only
writes); all failure modes stay loud.

## P2 — metric memory + thresholds (agreed; named, sized after P1)

- Persist a **scorecard snapshot per cycle** (one row per cycle-report edge,
  same mechanical moment ADR 0006 uses) → console chart of merge-rate /
  verdict mix / first-pass hit rate / tokens-per-merged-PR over time. The
  5-night watch becomes chartable instead of anecdotal.
- **Measurement only in P2** (Resolved O3): no threshold pings yet.
  **P2.1 — threshold pings** (merge-rate drop, cognition-timeout spike,
  clean-cycle-rate drop) ships after ~2 weeks of snapshot baseline, values
  chosen from the data.
- **Widen self-triage** beyond the `db_size` allowlist so recurring alerts
  arrive deduped with a proposed fix.

## P3 — scheduled evals + notify hardening (all three in — Resolved O5)

- **Nightly stub-mode sandbox-E2E** on the VPS runner (free, deterministic);
  failures ping.
- **Weekly real-pipeline eval burn** (Resolved O4): `run_all --cognition
  claude`, hard token cap, skipped while the account is quota-paused.
- **Notify hardening**: bounded retry on the goal-layer owner path (the task
  path already has it), plus one shared dedup/rate-limit helper replacing the
  per-site flags.

## Resolved (clarify step, 2026-08-12 — all answered by Denys in-session)

- **O1 — restart authority: PING-ONLY first.** O5 pings via the relay; the
  human restarts. Auto-restart is a named follow-up PR, unlocked only after
  O5 has a few correct detections on record (a wrong automatic restart
  during an in-flight sandboxed task is the riskier failure).
- **O2 — goal-level detectors: HYGIENE NOW, DECIDE AFTER THE WATCH.** Keep
  O1–O4 + playbooks, but fix them in P1 (terminal-phase filter kills the
  zombie incidents; dispatch-hold suppression kills the held≠stalled false
  positives). The strip-vs-keep decision is explicitly deferred to after the
  ledger 5-night watch, owner Denys — the watch supplies the evidence on
  whether O1–O4 ever catch anything devclaw's own pings miss. (Deliberately
  NOT the drafter's strip recommendation — evidence over doctrine.)
- **O3 — P2 ships MEASUREMENT ONLY.** Per-cycle snapshots + the console
  trend chart; NO threshold pings in P2. Thresholds become **P2.1**, chosen
  from ~2 weeks of baseline data — thresholds invented without a baseline
  are noise.
- **O4 — real-eval burn: WEEKLY, capped, pause-aware.** One
  `run_all --cognition claude` run per week with a hard token cap, skipped
  entirely while the account is quota-paused.
- **O5 — P3 scope: ALL THREE pieces in.** Nightly stub-E2E + the weekly
  real burn + notify retry/dedup. The "mostly agree" carried no surviving
  reservation once the pieces were named separately.

## Out of scope

External APM/OTel/log-aggregation stacks; multi-tenant console auth; closing
the self-fix loop (FIX stays human-gated by design); anything that adds a
tick-path LLM call.

## Invariant references

Zero-token idle guard, single-writer state, fail-closed gates, OAuth-only —
all per `CLAUDE.md`; nothing here asks to change an invariant. Ops-agent
boundary rules (outside-in reads, narrow MCP authority) stay as designed.
