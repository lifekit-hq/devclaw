# Feature Specification: The worker reads the loop's own facts

**Feature Branch**: `043-worker-reads-the-loop`

**Created**: 2026-09-09

**Status**: Draft

**Input**: User description: "The worker can read the loop's own facts. Today a devclaw worker runs in a sandbox knowing only its dispatch brief (measured live: 2,846 chars on issue-493's first dispatch, 4,891 on the retry) — it cannot see doctor's verdict on the instance it runs inside, its own goal's history and prior verdicts, or the problems catalog. […] devclaw is already an MCP server and the worker already speaks MCP, so this is supplying a FACT (constitution IX order: fact → instruction → brake), not adding a brake."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **stopped when it shouldn't**, primarily; **ran and
  produced garbage** secondarily. A worker that cannot see the instance it runs
  inside reports gaps that are already closed and stops the project; a worker
  that cannot see why the last round was refused repeats it.
- **Number that shows it**: clean-cycle rate **3/11** and `mechanical:env`
  wedging **5 of those 11** cycles (`get_loop_health`, 2026-09-09); the
  `block/env_deficiency` + `mechanical:env` catalog rows, **21+ recurrences in
  14 days**. Secondary: `convergence.first_pass_rate` **0.222** and
  `rounds_median` **3** (`get_scorecard_metrics`, 336h).
- **Cut when**: a story is deleted if, one month after it ships, the number it
  names has not moved AND the fact it exposes appears in no worker transcript —
  exposing a fact nobody reads is weight, not capability. The whole spec is cut
  if worker reads are observed to *cause* a regression the brief did not: a
  worker that treats a loop fact as licence to skip its own verification.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The worker sees the environment it actually runs in (Priority: P1)

A worker hits a tool failure that looks environmental — `npm ci` 401s, a
credential seems absent. Today its only move is to declare
`BLOCKED: env — <item>`, which parks the whole project until a human or a probe
disagrees. With the instance's capability facts readable, the worker checks
whether devclaw already believes that capability is healthy **before** it
declares a project-wide stop, and its report either carries that contradiction
as evidence or is never filed at all.

**Why this priority**: This is the class that parked three goals on 2026-09-09
(`fs-557-remove-sandbox-lore`, `scanner-broad-universe`,
`issue-493-fix-hardcoded-test`) on a `NODE_AUTH_TOKEN` hold while doctor read
that same credential `ok — set, well-formed, and accepted by GitHub (HTTP
200)`. It is the single largest devclaw-caused idle contributor after the
dispatch cap, and it is pure missing-fact: no judgment, no new brake.

**Independent Test**: Dispatch a task into a sandbox against an instance whose
capability probes are green, have the worker attempt an env-shaped failure
report, and observe from the running service that the report is either
suppressed or carries the contradicting instance fact. Delivers value alone:
the false-stop class disappears without any other story shipping.

**Acceptance Scenarios**:

1. **Given** the instance's capability probe for a credential reads green,
   **When** a worker in a sandbox reads the loop's environment facts,
   **Then** it receives that green verdict with its evidence and probe id, and
   the id matches the one doctor and the `mechanical:env` hold print.
2. **Given** a worker prepares an env deficiency report for a capability the
   instance reads green, **When** the report is submitted,
   **Then** the run records that the worker was shown the contradicting fact,
   and the report does not place a project-wide hold on that capability alone.
3. **Given** a capability probe reads red or unknown, **When** the worker reads
   the same facts, **Then** it receives red/unknown — an unrunnable probe is
   never presented as health, and the existing hold path is unchanged.

---

### User Story 2 - The worker sees why the last round was refused (Priority: P2)

A goal's second and third increments run in fresh sandboxes. Today what crosses
the boundary is a compact line per prior increment — PR number, verify-gate
pass/fail, 200 characters of error. The done-gate's verdict, its per-clause
judgment, and its rationale do not. The worker can read them: what was claimed
done, which clauses the gate judged unsatisfied, and on what evidence.

**Why this priority**: The done-gate returned `off_track` **33 times out of 66
calls** with `on_track` exactly **0** in the 14 days to 2026-09-09;
`rounds_median` is 3 and `rounds_max` 12. Meanwhile `decided_merge_rate` is
**0.9535** — what ships is good, so the loss is convergence, not quality. It is
P2 rather than P1 because it improves rounds rather than removing stops, and
because P1 must prove the read surface is safe first.

**Independent Test**: Run a goal to a refused done-proposal, dispatch the next
increment, and observe from the running service that the worker's session read
the prior verdict and its unsatisfied clauses. Delivers value alone: fewer
rounds per goal even with P1 and P3 absent.

**Acceptance Scenarios**:

1. **Given** a goal whose previous done-proposal was refused, **When** the next
   increment's worker reads the goal's own history, **Then** it receives the
   verdict, the per-clause satisfied/unsatisfied judgment, and the rationale
   recorded for that round.
2. **Given** a worker reads its goal's history, **When** the history contains a
   previous worker's free-text self-report, **Then** that text is NOT returned —
   only devclaw-controlled facts cross the boundary (spec 012's rule holds).
3. **Given** a goal has no prior refused proposal, **When** its history is read,
   **Then** an explicit empty answer is returned, never a fabricated one.

---

### User Story 3 - The worker sees what devclaw keeps hitting on this project (Priority: P3)

The deduplicated problems catalog records what recurs: which failures this
project produces, how often, whether they recovered or were terminal. A worker
can read the rows for its own project and avoid re-walking a path that has
failed the same way ten times.

**Why this priority**: Real but the weakest evidence of the three — the catalog
is a diagnosis surface built for the owner, and it is not yet known that a
worker acts differently for having read it. It ships last so its usefulness is
judged against P1 and P2 already in place, and it is the first candidate for
the cut rule above.

**Independent Test**: Seed a project's catalog with a recurring row, dispatch a
worker, and observe from the running service that the row was read and scoped
to that project only.

**Acceptance Scenarios**:

1. **Given** a project's catalog holds recurring rows, **When** its worker reads
   the catalog, **Then** it receives only that project's rows, with their
   recurrence and recovered/terminal counts.
2. **Given** another project has catalog rows, **When** a worker reads the
   catalog, **Then** those rows are absent — a sandbox never learns another
   project's failures.

---

### Edge Cases

- **The read surface is unreachable** (host down, network denied, token
  rejected). The worker MUST proceed on its brief exactly as it does today —
  the facts are an enrichment, never a precondition. A dispatch that cannot
  reach them is a normal dispatch, not a failed one.
- **The read surface is slow.** A read that does not answer within its budget is
  abandoned and the run continues; the loop's facts never hold a worker's turn
  open, and a slow read must not push a session toward the wall-clock timeout
  that already produces terminal failures.
- **The worker asks for another goal's state**, or for a write verb. Refused,
  and the refusal is recorded — an attempted out-of-scope read is a signal about
  the worker's instructions, not a silent no-op.
- **The facts and the sandbox genuinely disagree** (probe green, credential
  really absent in-container). The worker's direct observation still wins for
  its own execution; the contradiction is recorded so the probe can be fixed.
  This spec must not teach a worker to disbelieve its own tools.
- **A goal's history is large** (12 rounds). The read is bounded and says it is
  bounded, the same way the prior-increments section is tail-kept today.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A worker running in a sandbox MUST be able to read a bounded set
  of devclaw-controlled facts about the instance and the goal it is executing.
- **FR-002**: The surface MUST be **read-only**. No verb that mutates goal,
  task, project, or instance state is reachable from a sandbox — specifically
  not `steer_goal`, `resume_goal`, `cancel_goal`, `decide`,
  `correct_implementation`, `dispatch_task`, or any deploy/schedule control.
- **FR-003**: A worker MUST reach only its OWN goal's state and its own
  project's rows. Another goal's or project's state MUST be unreachable, not
  merely unadvertised.
- **FR-004**: Every fact returned MUST be devclaw-controlled. A previous
  worker's unverified self-report MUST NOT be returned (the spec 012 rule that
  one worker's claim never becomes the next worker's premise is unchanged).
- **FR-005**: The surface MUST be reached **through the runner**, not by the
  sandbox itself: the agent asks over the existing agent channel, `runner.py`
  relays the request to the host on its already-open line-delimited-JSON lane,
  and the host answers. **No new network path out of the container is opened.**
  (Decided with Denys, `/speckit-clarify` Q1, 2026-09-09 — option B.)
- **FR-006**: Access MUST be scoped per task and MUST expire with the task. A
  credential or handle that outlives its run, or that works for a different
  run, is a fence breach.
- **FR-007**: The credential (if any) that authorises the read MUST be declared
  in the ONE credentials registry with its least-privilege scope and the hops it
  crosses, like every other credential (`devclaw/credentials.py`, spec 042).
  `ANTHROPIC_*` keys remain refused at every hop.
- **FR-008**: Reads MUST cost zero cognition. Serving a worker's read MUST NOT
  wake the goal loop, dispatch a model call, or violate the zero-token idle
  guarantee for an idle instance.
- **FR-009**: Reads MUST NOT write goal or task state. The single-writer rule
  (only the TaskQueue mutates task rows) is unchanged; recording that a read
  happened is an append to the event log, never a state mutation.
- **FR-010**: An unreachable, refused, or slow read MUST degrade to "the worker
  proceeds on its brief". It MUST NOT fail the task, block the goal, or place
  any hold.
- **FR-011**: Every read MUST be observable after the fact — which facts a run
  read is part of that run's record, so a change in worker behaviour can be
  attributed and the cut rule can be evaluated.
- **FR-012**: The worker-facing instruction for when to consult these facts MUST
  live in exactly ONE home (`runner/skills/`), consistent with the
  one-home-for-worker-instructions invariant. No second copy.
- **FR-013**: An env deficiency report for a capability the instance reads green
  MUST still be **filed**, carrying the contradicting instance fact as evidence,
  and MUST NOT place a project-wide hold on that capability on its own — the
  next probe sweep decides. (Decided with Denys, `/speckit-clarify` Q2,
  2026-09-09.) Suppressing the report was rejected: if the probe is the thing
  that is wrong, suppression makes a real gap invisible and the worker fails
  with nothing said. Keeping the hold was rejected: it re-creates the
  false-stop class removed on 2026-09-09 and puts SC-001 back at risk.
- **FR-013a**: The withheld hold MUST be re-decidable without a human: the
  capability is re-probed on the normal sweep, and a red result places the hold
  then. A withheld hold that nothing re-checks would be the same defect this
  spec is named after — a brake, or a non-brake, that cannot observe its own
  condition.
- **FR-014**: The facts exposed MUST NOT include instance secrets, credential
  values, another project's identifiers, or owner-private content. Shape and
  status only, never values.

### Key Entities

- **Instance environment fact**: one capability's id, status
  (green/red/unknown), evidence, and remedy — the same probe row doctor and the
  `mechanical:env` hold already read, so all three surfaces tell one story.
- **Goal round record**: for one done-proposal — the verdict, the per-clause
  satisfied/unsatisfied judgment with its evidence, and the rationale. Excludes
  worker free text.
- **Project problem row**: a deduplicated recurring failure for one project —
  category, kind, normalised message, recurrence count, recovered/terminal
  split, and its filed-issue lifecycle.
- **Task-scoped read grant**: the authority one running task has to read the
  above, bounded to that task's goal and project and expiring with the task.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero project-wide environment holds are placed for a capability
  the instance simultaneously reads healthy. Baseline: 3 goals so parked on
  2026-09-09, 21+ catalog recurrences in 14 days.
- **SC-002**: `mechanical:env` wedges no more than 1 of any 11 consecutive run
  cycles. Baseline: 5 of 11.
- **SC-003**: Clean-cycle rate reaches 6/11 or better over a rolling window.
  Baseline: 3/11.
- **SC-004**: `rounds_median` falls to 2 or below, and `first_pass_rate` rises
  above 0.35, without `decided_merge_rate` falling below its current 0.95 —
  fewer rounds must not mean weaker work.
- **SC-005**: No sandbox performs a state-changing operation through this
  surface, and no sandbox reads another goal's or project's state, across every
  run in the measurement window. This is a fence criterion: one occurrence is a
  failure of the whole spec, not a degraded score.
- **SC-006**: A run whose read surface is unreachable completes at the same rate
  as one whose reads succeed — the facts are never load-bearing for a dispatch.
- **SC-007**: An idle instance still costs zero cognition calls with the surface
  live.

## Assumptions

- **The problems catalog a worker sees is scoped to its own project.** Least
  privilege; a sandbox has no business knowing another project's failure
  history. (Assumed rather than asked — no reasonable case for the alternative.)
- **This spec is read-only and does not give the worker a way to ask a
  question.** The recourse gap — a worker that can ask a bounded question
  mid-run instead of dying into a gate — is real and is deliberately a separate
  arc. Conflating them would make this spec unshippable.
- **The dispatch brief is unchanged.** These facts are pulled when the worker
  wants them, not pushed into an already-large brief; the brief's size is
  itself a known cost (the `claude --print` failure class scales with input).
- **The existing probe sweep is the source of environment truth.** No new probe
  runner is introduced; this reads rows that already exist and are already
  refreshed once per heartbeat sweep.
- **`decided_merge_rate` at 0.95 means output quality is not the problem.** The
  spec targets stops and rounds, not the quality of a delivered increment.
- **Worker-side adoption is an instruction, not a brake.** Whether the worker
  consults these facts is the agent's judgment guided by one line in a worker
  skill, measured by an eval — devclaw supplies the fact and does not police
  its use (constitution IX).

## Open questions for `/speckit-clarify`

**Q1 — How does the sandbox reach the loop's facts?** **ANSWERED 2026-09-09
(Denys): option B — the runner proxies.** The fence is unchanged, devclaw owns
both ends of the lane, and mid-run freshness is kept; the cost accepted is one
request/response lane added to the runner protocol. A and C are recorded below
as the rejected alternatives and their reasons — this spec is the direction
memory, so they stay.

| Option | Shape | Verdict |
|--------|-------|---------|
| A | **Live MCP over a host-reachable endpoint**, task-scoped token, read-only tool subset | **Rejected.** Full value, but it opens a network path from the sandbox back into devclaw. That turns SC-005 from a structural guarantee into something enforced by auth scoping — a fence we would then have to defend forever, bought for freshness B already delivers. |
| B | **The runner proxies** — the worker asks over the existing agent channel, the runner relays, the host answers | **CHOSEN.** Fence unchanged, both ends devclaw-owned, mid-run freshness kept. SC-005 is satisfied by construction rather than by policy. Cost: one new request/response lane in the runner protocol. |
| C | **Pushed at dispatch** — facts materialised into the sandbox as files at task start | **Rejected.** Lightest, but freezes the facts at t=0: a probe that goes green *during* a run is invisible, which is precisely the 2026-09-09 failure this spec exists to remove. It also grows every dispatch, and dispatch size is already a known cost (the `claude --print` failure class scales with input). |

**What B implies for the plan** (not decided here, flagged for `/speckit-plan`):
the runner's agent-drive seam stays model-agnostic (spec 011) — the ask must
ride a mechanism any ACP-speaking agent can use, not a vendor tool-wiring; and
the new lane is part of the protocol devclaw owns, so it is one of the five
things software is allowed to own (constitution IX).

**Q2 — Does an env report contradicted by a green probe get suppressed, or
recorded-and-downgraded?** **ANSWERED 2026-09-09 (Denys): recorded, hold
withheld.** The report is filed with the contradiction attached; the
project-wide hold is not placed on that report alone; the next probe sweep
decides. Rationale and the two rejected readings are in FR-013/FR-013a.

**No open questions remain. Ready for `/speckit-plan`.**
