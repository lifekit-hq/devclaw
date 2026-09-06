# Feature Specification: Loop-shape demolition — the worker closes its own findings; the host keeps five domains

**Feature Branch**: `037-loop-shape-demolition`

**Created**: 2026-09-06

**Status**: Draft — awaiting `/speckit-clarify` with Denys (three open questions below). Supersedes the closed 037 calibration-corpus draft (PR #841).

**Input**: User description: "Does the current devclaw workflow make sense, with all those review gates after each task? devclaw is a wrapper of claude — it should structure the working agent, not make it stupid." (Denys, 2026-09-06; ruled "do it" on the four cuts below)

## Why (context the requirements hang off)

Fourteen days of live data (scorecard, 336h window, 2026-09-06): 43 PRs
opened, 37 merged, decided-merge 0.95 — the work that ships is good. But 21
goals closed with a first-pass rate of 0.29, a median of 2 done-gate
rounds and a maximum of 12; the owner steered 17 times and committed by
hand on goal branches 19 times; the evaluator ran 70 times and graded 54 of
68 rounds "concerns". The problems catalog's largest rows are the harness's
own cognition failing: review-gate OOM 117, trend-detector exit 21,
summary exit 12, review timeouts 12.

Six goal logs read in full (fs-479, devclaw-030, fs-539, lkc-22,
lkd-honest-card, fs-432) show the mechanism. The mechanical gates (verify,
materialize, change class, test integrity, CI as verdict, merge-on-close)
are cheap, deterministic and never wedged a task wrongly in this window.
The done-gate judge is not wrong either: every hold cited a real, specific
gap a senior reviewer would also flag (half an issue deferred to "a future
slice"; a probe TTL longer than the heartbeat by construction; a
whitespace-padded id that fails open).

The cost is the **shape**. Each finding costs a full cycle: a 20–40 minute
worker session, then a 10–30 minute independent review session in a second
sandbox, then a `--print` evaluator, a hold, and a fresh worker with no
memory except a brief that grew from 9k to 22k characters of second-hand
summaries — devclaw-030 needed 18 dispatches and 8 gate rounds to land one
spec, one gap per round. Two agents that never talk, mediated by prompts and
parsers, with amnesia between hops: that is the "wrapper making Claude
stupid" the owner named. A single session with the reviewer's findings in
front of it fixes five gaps in twenty minutes.

Constitution IX draws the line this spec applies: software owns safety,
money, state, the verdict of record and the protocol; the agent owns
everything that varies per repo. Four host cognition roles sit outside
those five domains and earn nothing measurable; the review of an increment
is repo-varying judgment that belongs to the agent. The demolition is a
**relocation**: the review moves into the worker as its first pass, the host
keeps exactly one independent read per done proposal, and the roles with no
consumer are deleted outright.

## Clarifications

### Open for the clarify session (in priority order)

- **Q1 — The host's independent done-check after the worker self-reviews.**
  Today a done proposal dispatches a separate `review_repository` sandbox
  session whose report the `--print` evaluator judges against the pinned
  clauses. Options: (A) keep it — one independent read per proposal, the
  #630 class (the agent chooses what to record) stays covered, and the
  expectation is that it passes first time because the worker already
  closed the findings; (B) drop the second sandbox and let the evaluator
  judge the worker's own self-review report plus CI — cheaper by one
  session per proposal, but the only grounded read is the worker's own
  claim, which the harness doctrine explicitly refuses as evidence.
  [NEEDS CLARIFICATION: keep the independent review session (A) or judge the worker's self-report (B)]
- **Q2 — Is the in-session self-review an instruction or a protocol field?**
  Constitution IX closes a gap with an instruction first and a brake only
  inside the five domains; the protocol IS one of the five. Options: (A)
  instruction only — one skill section, checked by the scorecard's rounds
  number; (B) the worker's result payload carries a `self_review` record
  (findings found, findings closed) and the host refuses a done proposal
  without one — a protocol brake, mechanical, zero cognition.
  [NEEDS CLARIFICATION: instruction only (A) or a required protocol field (B)]
- **Q3 — The `evaluate_goal` verb.** The on-demand direction evaluation is
  the fourth role being cut; the MCP tool is what the ops agent's nightly
  cron calls, and every nightly call on a pointer goal has written
  "done_when is literally unspecified" since spec 019 (the #795 class).
  Options: (A) delete the verb and the tool — the nightly cron on the
  OpenClaw side is retired with it; (B) keep the tool name as a zero-cognition
  status read (phase, in-flight, last done-gate verdict) so the cron keeps
  working and costs nothing.
  [NEEDS CLARIFICATION: delete the verb (A) or keep it as a mechanical read (B)]

### Rejected alternatives (direction memory)

- **A calibration corpus for the judge** (the closed 037 draft, PR #841).
  Rejected by ruling: devclaw does not test the cloud model's cognition
  (feedback ruling "test modules, not the model"), the loop's problem is
  its shape rather than the judge's calibration, and "was the hold wrong?"
  is answerable mechanically from persisted data (a hold followed by a close
  over an empty change span) if it is ever needed.
- **Cutting the done-gate itself.** Rejected: every hold in the six logs
  cited a real gap; fs-479's twelve rounds were a wrong contract plus hand
  edits on main, both already answered (spec 031 Problems, one goal one
  checkout). Done stays a proposal gated on grounded evaluation
  (constitution V).
- **Flag-gating the four roles off instead of deleting them.** Rejected:
  a disabled role is a fork that rots (the #610 class); the symmetric
  ratchet removes the behaviour and its tests together.
- **Keeping the plain-language summary "for a non-technical owner".**
  Rejected: the owner reads logs and PR bodies; the summarizer added 12
  failure rows and a second wording of every ping.
- **A second review model with a different prompt as the in-session
  reviewer.** Rejected: the fresh-context subagent of the same agent is the
  standard practice (a reviewer that did not write the diff); a devclaw-
  specific reviewer needs a reason the standard one cannot give.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The increment closes its own review findings before proposing done (Priority: P1)

The worker finishes an increment, then reviews its own diff against the
contract's pinned clauses in a fresh context (a subagent that did not write
the diff), fixes what the review finds, and repeats until the review is
clean — inside the same sandbox session, with the whole repo and its own
working memory in front of it. Only then does it verify, commit and propose
done. Its summary states what the self-review found and closed.

**Why this priority**: this is the relocation that collapses rounds. Today's
serial one-finding-per-round loop exists only because the reviewer and the
fixer are different sessions. It is the whole reason the owner asked.

**Independent Test**: on a stub goal whose contract has three clauses and a
seeded gap in one, the worker's single session ends with the gap closed and
a summary naming it; the host's done-check then passes on the first
proposal. On the live instance: rounds median over the next 14 days.

**Acceptance Scenarios**:

1. **Given** an increment whose diff leaves one pinned clause unsatisfied,
   **When** the worker's self-review runs, **Then** the finding is fixed in
   the same session and the proposal's summary names the finding and the
   fix.
2. **Given** a self-review that finds nothing, **When** the worker proposes
   done, **Then** the summary says so and no extra session was spent.
3. **Given** the worker cannot close a finding (a contract contradiction or
   a missing capability), **When** it proposes, **Then** it reports the open
   finding as a BLOCKED / Problem-shaped fact rather than proposing done
   over it — the existing honest-block path, unchanged.
4. **Given** the self-review loop, **When** the context tripwire fires,
   **Then** the partial lands as today and the next increment resumes from
   the branch — the loop never overrides the context brake.

---

### User Story 2 - Four host cognition roles retire (Priority: P1)

The plain-language notification summarizer, the self-triage interceptor,
the trend detector, and the direction evaluator outside the done-gate (the
cadence auto-eval and the on-demand verb) are removed — prompts, parsers,
callers, config flags, MCP tools, tests, docs, and their doctor and
scorecard surfaces — in one PR per role or one PR for all four.

**Why this priority**: four prompts, four parsers, four failure classes and
three env flags with no measured consumer. Every one costs quota on the
least reliable call class in the system and adds an edge case to every
incident. Deleting them is the cheapest complexity reduction available and
carries no behaviour risk: nothing downstream reads their output as a
decision.

**Independent Test**: after the cut, the host cognition roles are exactly
the evaluator (done-gate only), intake readiness, admission lint, the
strict-only review gate and browser reachability; `pytest` is green with
the roles' tests removed; the problems catalog gains no new
`summary`/`triage`/`trend-detector`/on-demand rows; the zero-token idle
guard tests are untouched and green.

**Acceptance Scenarios**:

1. **Given** an owner ping fires, **When** it is delivered, **Then** it is
   the raw text — no rewrite, no triage proposal, one cognition call fewer.
2. **Given** a goal on a daily cadence with nothing in flight, **When** the
   cadence is due, **Then** no evaluator call happens; the done-gate remains
   the only evaluator caller and fires only on a done proposal.
3. **Given** the nightly ops cron, **When** it calls the retired verb,
   **Then** the outcome is whatever Q3 decides — a clear tool-not-found or
   a mechanical status read — never a cognition call.
4. **Given** the trend detector's vault file and the `review_trends` tool,
   **When** the cut lands, **Then** both are gone and the docs no longer
   describe them.

---

### User Story 3 - devclaw's own goals run under trust (Priority: P2)

The devclaw project's manifest default strictness moves from `strict` to
`trust`, so the per-increment adversarial review gate — the single largest
failure source in the catalog — no longer runs on devclaw's own increments.
CI plus the done-gate plus the owner's review of every PR are the surface,
as they already are for every other project.

**Why this priority**: one manifest line; removes 117 OOM rows' worth of
exposure from the repo where the owner already reviews everything. P2 only
because it is a setting, not a mechanism.

**Independent Test**: a devclaw goal created after the change resolves to
`trust`; the gate-outcomes event for its increments shows `review` not
consulted; `set_goal_strictness` can still opt one goal back to strict.

**Acceptance Scenarios**:

1. **Given** the manifest change on main, **When** a devclaw goal is
   dispatched, **Then** its effective strictness is `trust` and the review
   gate is not consulted.
2. **Given** an owner who wants strict on one goal, **When** they set it,
   **Then** the per-goal override still wins — the dial is unchanged.

### Edge Cases

- The worker's self-review subagent is unavailable in the sandbox agent
  (an ACP agent without subagents): the worker reviews in its own context
  and says so in the summary — thinner independence, never a skipped step.
- A self-review that keeps finding new things: the loop is bounded by the
  context tripwire and the task timeout, both existing brakes; the worker
  lands a partial rather than looping.
- A retired role is referenced by a persisted row (a `trend` problem
  category, a `summary` cognition trace kind): reads stay valid, the writer
  is gone; no migration.
- The ops cron calls the retired verb before the OpenClaw side is updated:
  Q3 decides; either way it is loud and costs nothing.
- A project with `strictnessDefault: strict` in its own manifest (not
  devclaw) is unaffected — US3 changes devclaw's manifest, not the dial.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker's code-writing skill MUST instruct one self-review
  pass against the pinned contract before proposing done, in a fresh context
  where available, with findings fixed in the same session and named in the
  summary. The instruction lives in exactly one skill section (one home).
- **FR-002**: The host MUST NOT gain a new cognition call from US1; the
  self-review is the worker's, inside its existing session budget.
- **FR-003**: The plain-language summarizer, the self-triage interceptor,
  the trend detector and the direction evaluator outside the done-gate MUST
  be removed, together with their prompts, parsers, config flags
  (`DEVCLAW_GOAL_PLAIN_SUMMARY`, `DEVCLAW_TREND_ENABLED`, the self-triage
  toggle), MCP tools (`review_trends`, `evaluate_goal` per Q3), scorecard
  and doctor surfaces, tests and docs. The symmetric ratchet applies:
  behaviour and tests leave in the same PR.
- **FR-004**: After US2 the evaluator MUST have exactly one caller: the
  done-gate on a done proposal. No cadence tick and no verb spends an
  evaluator call.
- **FR-005**: The zero-token idle guard, the fail-closed gates, the pauses,
  the materialize span, CI as verdict, merge-on-close, pinned clauses and
  the churn brake MUST be untouched by this spec; their tripwire tests stay
  green unchanged.
- **FR-006**: The devclaw project's manifest default strictness MUST be
  `trust`; the per-goal dial and `set_goal_strictness` are unchanged.
- **FR-007**: The docs that describe the retired roles (`CLAUDE.md` layer-3
  row, `docs/architecture.md`, `docs/reference/env-vars.md`, the INDEX
  currency tags, the cognition-prompts rule's module list) MUST be corrected
  in the same PRs; `tests/test_harness_docs_map.py` holds the line.
- **FR-008**: Every cut MUST be measured by the existing scorecard and
  eng-health, never by a new instrument: rounds median, first-pass rate,
  interventions per achieved goal, evaluator calls per closed goal, tokens
  per merged PR, and the problems-catalog rows for the retired roles.

### Key Entities

- **Self-review record**: what the worker's in-session review found and
  closed, carried in the increment summary (and, per Q2, possibly in the
  result payload).
- **Host cognition role**: a `--print` prompt + parser + caller; after this
  spec the set is evaluator (done-gate), intake readiness, admission lint,
  review gate (strict only), browser reachability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

Baseline is the 2026-09-06 scorecard (336h window). Read again 14 days after
the last PR of this spec deploys.

- **SC-001**: Done-gate rounds median 2 → 1; first-pass rate 0.29 → at least
  0.60 (the ratchet target of 0.70 is the goal, 0.60 is the floor that
  proves the shape change worked).
- **SC-002**: Owner interventions per achieved goal 1.29 → below 0.5, with
  hand commits on goal branches trending to zero.
- **SC-003**: Evaluator calls per closed goal 3.3 → at most 1.5.
- **SC-004**: Host cognition roles 7 → 5; the problems catalog gains zero
  new rows for `summary`, `triage`, `trend-detector` or on-demand direction;
  the review-gate OOM row gains zero new entries from devclaw goals.
- **SC-005**: Tokens per merged PR no worse than the baseline (14.7k) — the
  self-review spends inside the session, not on top of it.
- **SC-006**: Net source lines of `devclaw/` go down; eng-health's
  `prompt_static_tokens_total` and `config_env_vars` both decrease.

## Assumptions

- The sandbox agent (Claude Code over ACP) can spawn a fresh-context
  subagent for the self-review; if not, the worker reviews in its own
  context and the summary says so (edge case above).
- Spec 034 (worker file memory) is the companion demolition — it removes
  the repo-notes hand-back lane that inflates the brief 15× — and is armed
  immediately after this spec lands. It is a dependency of SC-005, not in
  this spec's scope.
- The ops agent's nightly cron is on the OpenClaw side (lifekit-stack,
  GitOps); retiring or repointing it is a one-line change there, done in the
  same arc.
- The done-gate's structural axis ("concerns" on 80% of rounds) is left as
  is; under trust it advises and ships. Whether it earns its keep is a
  question for the next eng-health run, not this spec.
- No new tests are minted for the removals; the zero-token, fail-closed and
  CAS tripwires already pin what must survive.
