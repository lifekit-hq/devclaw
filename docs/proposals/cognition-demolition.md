# Proposal — cognition demolition: collapse 8 control-plane cognition boundaries to 1, migrate durable plan-state to a worker-owned `PLAN.md` on the target repo

- **Status:** **PIVOTED + LOCKED (direction) — 2026-08-05.** P1 (mid-flight-eval cut)
  SHIPPED 2026-08-03 (#456). The plan-state home is now a **plain worker-owned
  `PLAN.md` file in the target repo — NOT the wayfinder GitHub-issue machinery** that
  §3/§5 originally locked. Denys's end-of-2026-08-03 call: *"radical mode — go thinner
  with PLAN.md, don't ask me."* This is a THINNER version of the same demolition — same
  thesis (§1), same cut list (§4), same trust-input/verify-output principle (§3a); only
  the plan-state's HOME changes (a file the worker maintains, not an issue tree the
  control plane parses a frontier from). It **MOOTS the merged P2 host-side foundation**
  (`wayfinder.py` parse/selectors · `wayfinder_gh.py` gh adapter · the `wayfinder_map`
  goal_docs cache, #457/#458/#459) — those stay in main, inert and removable, but are no
  longer load-bearing. The thin-PLAN.md direction is LOCKED (Denys confirmed 2026-08-05);
  the remaining `[OPEN]`s are the ones the pivot did not moot (§6). Captured from the
  multi-message brainstorm + the pivot
  (vault `~/memory/projects/devclaw/demolition-spine-2026-08-03.md`, "PIVOT + MODE
  CHANGE" section). **No further architecture forks open** — the pivot collapsed them.
  This is still the largest direction change since v1→v2; it amends the control-plane
  planning spine of [ADR 0003] and the single-writer-to-plan-state invariant (see
  Invariant impact).
- **Invariant impact:** **HIGH, and named as a headline, not a footnote.**
  1. **Single writer to state — amended for *plan* state, unchanged for *lifecycle*
     state.** Today the goal's plan ("what's next") is re-derived each tick by a
     control-plane LLM (the planner) and persisted goal-side (`GoalStore`). This
     proposal moves **plan-state** into a **`PLAN.md` file committed to the target
     repo, written solely by the worker** in-session; the control plane never
     re-plans — it dispatches "advance the goal, keep `PLAN.md` current" and reads
     nothing back for decisions except the surviving done-gate. Goal **lifecycle**
     state (investigating/executing/blocked/done) stays owned by `GoalStore` under
     the CAS'd `transition()` choke point — unchanged. The split is deliberate:
     cognition → worker, consumption → mechanism. (The `PLAN.md` travels with the
     clone — no external tracker dependency, no frontier for the control plane to
     parse; strictly thinner than the mooted gh-issue map.)
  2. **Zero-token idle guard — preserved, and strengthened.** The control plane no
     longer even reads the plan: the thin path is *dispatch a worker session +
     done-gate*, with NO per-tick `gh`/frontier read at all. The `FakeClaude.calls
     == 0` idle assertions must stay green — the planner-cut REMOVES a tick-path LLM
     call and adds none back.
  3. **Model-agnostic worker layer — preserved.** `PLAN.md` is a plain markdown
     file the worker reads and writes with ordinary file tools; the "how to
     maintain PLAN.md" guidance is plain markdown worker skill text, NOT the
     mattpocock Claude-Code skill installed into the sandbox (that would blow the
     curated-allowlist invariant). We borrow the *pattern* (plan-as-durable-doc,
     worker-owned), re-expressed.
  4. **"Done is a proposal gated on grounded evaluation" — preserved.** The
     done-gate evaluator is the ONE surviving cognition boundary; it reads the
     goal's destination (the thin `done_when`, and `PLAN.md` for context) against
     the repo (`[OPEN] O4`).
- **Date opened:** 2026-08-03 · **Authors:** Denys + Claude
- **Grounded on:** LOC measured at `main` @ `9165ced` (2026-08-03): `devclaw/` =
  33,444 py; `goal/` = 13,746 py; prompt corpus = 1,251 md. Cognition callers
  measured individually (§2). `measure_passrate.py` read at `main` — INTACT (the
  10-ticket `evals/baskets/v01-proof.json` basket, pin-SHA, pause-aware `_settle`),
  last real run Jul 20 → DORMANT, not rotted. Self-issue-filing (`DEVCLAW_SELF_REPO`,
  GATHER→FILE→FIX→CLOSE) LIVE — the `gh`-issue create/read/close plumbing already
  exists. wayfinder skill: `mattpocock/skills/skills/engineering/wayfinder/SKILL.md`.
- **Relates to / amends:**
  - **[ADR 0003] goal↔program unification** — this proposal takes the control-plane
    planning spine (grill→firm→decompose→next-action) that ADR 0003 unified and
    **relocates it into the worker**, leaving the ADR's ONE-primitive/ONE-dial goal
    surface intact. The re-evaluation cadence dial (`long_lived`/`one_shot`) survives;
    what changes is *where* the per-tick judgement lives. **Amends ADR 0003** — flagged.
  - **[ADR 0007] gate strictness dial** — the three review-shaped gates (adversarial
    review, browser-E2E, reachability) already default advisory under `trust`. This
    proposal takes them further to **advisory-or-away** (`[OPEN] O6`); the verify +
    test-integrity + done-gate stay always-hard. Extends, does not repeal, ADR 0007.
  - **[ADR 0004]/[ADR 0006] evals** — reviving `measure_passrate` as the live
    scoreboard is the **measurement precondition** for every cut here (a cut is
    only lockable if the pass-rate holds or rises). This proposal DEPENDS on the eval
    instrument; it does not modify it.
  - **`console-legibility.md` / trace_view (#453)** — `PLAN.md` in the repo is a
    direct answer to that thread's "the console renders IDs, not what happened":
    the plan-state becomes a committed, diffable file the worker actually writes,
    watchable in the repo and (later) renderable in the console.
  - **`foundation-checkpoint.md` (#432)** — both read from firming's derived
    `done_when`/asserts; the firming relocation (`[OPEN] O5`) must reconcile with it.
  - **`gap_operator_ux` (vault)** — steering stays `steer_goal` (an input the
    worker re-reads); the pivot does not add the issue-comment steering handle the
    gh-machinery would have (a thinner-but-less-UX trade, accepted with the pivot).

---

## 1. Why (the direction, in one paragraph)

devclaw's `goal/` layer grew to 13,746 lines and eight separate control-plane
cognition boundaries (planner, decomposer, progress-evaluator, firming, research,
world-research, summarizer, done-gate), each of which re-chews context into a
hand-written prompt and can hallucinate *independently*. That is the source of the
"unclear planning / hallucinating" Denys named as the enemy, and — via a fat
decomposer + planner — a contributor to the host-memory OOM. The demolition collapses
the eight to **one** (the done-gate) by moving planning/decomposition/research **into
the worker session** — the way you'd brief a subagent: *here's the question, go look*
— and giving the loop's durability a new, more legible home. The north-star yardstick
is Denys's: *"a durable, goal-persistent thing that survives any session/crashes, with
clear step-by-step planning without hallucinating — imagine it's me at the desk, but
instead of me it's devclaw, pursuing the goal each day."* Every cut is held against
that. The thesis is **preserved and made visible**, not touched.

## 2. Ground truth — the deletion budget, measured

Denominator: `devclaw/` = 33,444 py; `goal/` = 13,746 py; prompt corpus = 1,251 md.

**Hard delete — the six extra brains (py):** planner 397 · decomposer 166 · research
85 · world_research 112 · firming 516 · progress-half of evaluator ~250 (done-gate
survives) · summarizer (summary 54 + run_summary 185) 239 = **~1,765 py**.

**Their prompts (md):** decomposer.md 347 · firming.md 238 · goal-planner.md 124 ·
world-research 47 · research-discovery 32 · owner-summary 15 · scope-grill 45 ·
goal-evaluator half ~75 = **~923 md — ~75% of the entire 1,251-line prompt corpus.**

**Demote, not delete (the 3 gates → advisory, code stays default-off):** browser_gate
277 · reachability 116 · review gate (in `quality/__init__.py` 549) + prompts
(review-gate.md 81 + browser-reachability.md 49). **~0 deleted if advisory; +~600 py if
"away" (`[OPEN] O6`).**

**New code back (net-add):** thinner than the gh-issue version — a `long_lived`
mechanical dispatch handler (mirrors `_handle_one_shot_executing`) + the light pull-brief
+ a plain-markdown worker `PLAN.md` skill ≈ **<100 py + one skill file** (no parse, no
`gh` adapter, no cache — those were the mooted P2).

**NET ≈ 2,500 LOC deleted** — ~8% of the codebase, **~20% of the `goal/` layer** (the
layer that's *meant* to be thin). The headline is not the Python — it's that **~75% of
the hand-written cognition-prompt corpus evaporates**: the bloat was never the code, it
was the six prompt files written to spoon-feed six separate LLMs. Tick-plumbing
(`tick_context`/`settle`/`guards`/`dispatch`, `service`, `triage`) simplifies as a
secondary effect (unmeasured, real).

**The substrate is the repo itself:** `PLAN.md` is a file committed and pushed on the
delivery path the work already takes — no new substrate, no `gh`-issue plumbing, no cache.
(The earlier draft of this proposal locked the fuller wayfinder GitHub-issue machinery;
the 2026-08-05 pivot declined it for the plain file — see §3. The merged host-side
foundation for that version, #457/#458/#459, is now inert.)

## 3. The direction — the thin path (Denys, 2026-08-03 pivot; LOCKED 2026-08-05)

> **Durable plan-state is a plain `PLAN.md` file, worker-owned, committed to the target
> repo. The worker reads the repo + `PLAN.md`, plans IN-SESSION, updates `PLAN.md`, and
> does the work. The control plane NEVER re-plans and never parses a frontier — it
> mechanically dispatches "advance the goal, keep `PLAN.md` current" and closes only via
> the surviving done-gate. The planner LLM call dies as a consequence, not a goal.**

This SUPERSEDES the wayfinder GitHub-issue machinery that this section originally locked
(2026-08-03 daytime). Rationale for going thinner: *"here's the question, go look"* —
less to build, more to delete, no external-tracker dependency, no frontier-parsing code.
The gh-issue version's strengths (a shared-URL board, steering-by-comment) were real but
bought machinery and a GitHub-uptime dependency the pivot judged not worth it against a
file that travels with the clone.

- **The plan = a `PLAN.md` at the repo root** (destination · decisions-so-far · what's
  next / open questions · out-of-scope), maintained by the worker each session. It is the
  worker's own working memory made durable — the thing that survives the ephemeral session
  and lets the *next* session pick up where this one left off (thesis wire #2). Shape is
  worker guidance, not a control-plane-parsed schema.
- **The tick is mechanical AND blind to plan contents:** executing goal with work to do →
  dispatch ONE worker session briefed *"advance the goal; read and update `PLAN.md`"* →
  session settles → done-gate check (achieved? close : keep going). There is **no
  control-plane read of `PLAN.md`** — the plan is the worker's tool, not the control
  plane's input. This is strictly less code than parsing an issue frontier, and it keeps
  the tick zero-token.
- **Steering survives unchanged:** `steer_goal` appends to the goal's steering input; the
  worker session reads it at start and re-plans (updating `PLAN.md`) accordingly. Control
  plane writes an *input*, never the plan — single-writer-to-the-plan holds (the worker is
  the sole writer of `PLAN.md`); `goals-are-durable-no-field-patches` holds.
- **`PLAN.md` always on the target repo.** It is committed and pushed with the work, the
  same delivery path the code takes — no separate substrate, no "map home" resolution
  logic, no fallback. A repo devclaw can't push to can't host the goal anyway (delivery
  already fails loud there), so there is no new edge to block on.
- **The price is smaller:** no GitHub-API dependency, no rate-limit budget, no last-read
  cache to keep coherent. The one durable-state risk — a session that fails to write/commit
  `PLAN.md` — degrades gracefully: the next session re-reads the repo and re-plans from
  what's actually there (the code is the ground truth; `PLAN.md` is an accelerator, not the
  sole source of truth). A missing `PLAN.md` is "plan from scratch this session," not a block.

## 3a. The principle underneath — trust the input, verify the output (Denys, 2026-08-03)

The LOC count is a symptom; this is the disease. devclaw today **PUSHES** context — big
pre-chewed prompts and up-front interrogation — into every cognition step and every new
worker session, *because it distrusts the agent to find things out for itself*. That
distrust is the source of the ~105 KB prompt bloat, the fat decomposer/planner (OOM
pressure), and the firming grill. The demolition's deepest move is to flip push → **PULL**:

- **Context is pulled, not pushed.** A worker session gets a light, subagent-style brief —
  *goal + "read `PLAN.md` and the repo, then advance the goal"* — and pulls what it needs
  from durable places (`PLAN.md`, the repo's committed `AGENTS.md`, the repo
  itself), exactly the way a trusted subagent explores. No dossier is injected; the agent
  has tools and uses them. (This is how devclaw's *own* dev harness already spawns
  subagents — a focused prompt + a pointer, not the pre-chewed answer.)
- **Distrust does not vanish — it MIGRATES, from prompt-time to verify-time.** The
  always-hard mechanical gates (verify, test-integrity, done-gate) STAY; that is where
  distrust legitimately lives. The dogfood-integrity lesson (#358: a trusted worker WILL
  gut tests to go green and rationalize it in `AGENTS.md`) is why we can never trust the
  *output* on faith. So the rule is exact: **stop distrusting the INPUT (drop the injected
  context and the up-front grill); keep verifying the OUTPUT (the gates hold).** Trust in,
  verify out — the way you brief a good subagent *and still read what comes back*.

This is the through-line that makes every cut coherent: each of the six cut callers is a
place we pre-chewed context out of distrust; the surviving done-gate + mechanical gates
are where verification (earned distrust) stays.

## 4. Keep / demote / cut

| column | items | fate vs the thesis |
|---|---|---|
| **KEEP (the body)** | sandbox, delivery, console, MCP surface, queue, `loom`, state-store; the **done-gate evaluator** (grounded on the goal destination + the repo) | trust-neutral; the reason v1 was kept |
| **KEEP as mechanism** | tick/heartbeat loop; no-progress watchdog (fed a **mechanical** signal, `[OPEN] O1`); verify gate; test-integrity gate; block-and-escalate | the four durable-pursuit wires; must survive |
| **DEMOTE** | adversarial-review, browser-E2E, reachability gates → advisory (or away, `[OPEN] O6`) | quality gates, not pursuit; advisory *is* "don't interrupt me" (ADR 0007) |
| **CUT (dissolve into worker)** | planner, decomposer, research, world-research, summarizer; firming (`[OPEN] O5`); progress-half of evaluator | LLM-second-guessing / control-plane re-chewing; the plan-state migration is what makes these safe |

## 5. Sequencing (thin-path re-slice — 2026-08-05 pivot)

The pivot collapses the old P2 (issue-substrate) + P3 (frontier-walk) into a single, much
smaller change: there is no substrate to build and no frontier to parse. The migration is
"the worker maintains a file"; the cut is "delete the planner path and make the mechanical
dispatch the default."

- **Precondition (not a slice of this proposal):** revive `measure_passrate` as the live
  scoreboard — the **2/2 = 1.0 baseline** on pre-demolition main @ `9165ced` is established
  (2026-08-03); the planner cut is re-measured against it. Without it, every cut is
  faith-based demolition.
- **P1 — SHIPPED (#456, 8a24c2c):** cut the **per-tick mid-flight evaluator**
  (`_run_mid_flight_eval`). The mechanical brakes stand (zero-token no-progress watchdog,
  grounded done-gate, per-item circuit breaker).
- **P2 — the worker `PLAN.md` skill (thin, ≈ 1 PR).** Plain-markdown worker guidance:
  read the repo + `PLAN.md`, plan in-session, update `PLAN.md`, do the work, commit it on
  the delivery path. This is the rewrite of the (now-mooted) #460 wayfinder-issue skill for
  the file shape. Inert until P3 wires the brief. No control-plane code.
- **P3 — the planner cut + tick rewire (the load-bearing change, full invariant-guard).**
  A **mechanical "advance the goal / maintain `PLAN.md`" dispatch** for `long_lived` goals
  (mirrors `_handle_one_shot_executing`'s zero-LLM shape), with the done-TRIGGER resolved
  in O4 (worker session-`DONE` proposes; the grounded done-gate verifies). Lands in two
  steps to de-risk the hot path:
  - *P3a — additive + flag-gated:* the new path behind a default-off flag; the planner
    stays the default fallback. Stub-tested (the zero-token idle guard + the ralph-loop
    dispatch/verify cycle), no box needed. The light pull-brief lands at
    `openhands-runner/runner.py:384`.
  - *P3b — flip + delete:* after the box `measure_passrate` re-run proves the flagged path
    holds the **2/2 baseline**, flip the default and delete `planner.py` + `goal-planner.md`.
    The eval re-run is the merge gate for P3b.

  Migration order still load-bearing (`[OPEN] O3`): the worker `PLAN.md` skill (P2, #462)
  must land before the planner is cut.
- **P4 — decomposer + firming relocation, summarizer removal, the three gates
  advisory→away.** The bulk of the prompt-corpus deletion (~75%). Decomposer is the OOM
  fish (`[OPEN] O7`) — relocate into the worker session with care, never before P3.
  Firming collapses to the thin destination-agreement (`[OPEN] O5`).

## 6. `[OPEN]` — the clarify step

The architecture is settled (§3, thin-PLAN.md). The 2026-08-05 pivot resolved or mooted
most of the original `[OPEN]`s (they were gh-issue-machinery questions). What survives is
noted per item; the direction is LOCKED regardless (Denys, 2026-08-05).

- **[OPEN O1] Mechanical watchdog food — RESOLVED at grounding (2026-08-03).** The
  no-progress watchdog (`tick_guards.py:_check_no_progress`) is ALREADY a zero-token
  wall-clock check keyed on `last_progress_at` (bumped on delivery) — it never consumed
  the LLM `off_track`. So cutting the per-tick evaluator does NOT starve the brake; no
  new food is needed for P1. ("A delivery landed" is the mechanical signal, unchanged by
  the pivot — the thin path keeps the same delivery-keyed watchdog.) **RESOLVED — shipped
  in P1 (#456).**
- **[O2] MOOTED by the pivot.** There is no map read and no cache — the control plane
  never reads `PLAN.md` (§3). No cadence/caching seam to design. The zero-token guard is
  strengthened, not merely preserved (Invariant impact #2).
- **[OPEN O3] Migration order is load-bearing — STILL OPEN, now cheap.** P3 (planner cut)
  must NOT precede P2 (the worker `PLAN.md` skill exists), else the worker has no guidance
  to maintain the plan and durable-pursuit degrades in the gap. It's a written gate on the
  P3 tranche: land the skill (P2), then cut the planner (P3), same PR-order or stacked.
- **[O4] Done-gate destination + done-TRIGGER — RESOLVED for P3 (2026-08-05).** Two parts:
  - *Yardstick:* the done-gate reads the goal's `done_when` (thin destination-agreement,
    `[OPEN] O5`) against the repo. `PLAN.md` is available as context but is NOT the
    yardstick — the worker writes it, so gating on it would be trusting the output on faith
    (§3a). The done-gate keeps reading `done_when`, not `PLAN.md`. (Reconcile firming's
    `done_when` derivation with foundation-checkpoint at P4.)
  - *Trigger (the planner-cut's real gap):* with no planner to propose "done", the worker's
    **session-level** `STATUS: DONE` (its hand-back contract, `_RETURN_CONTRACT`) + its
    acceptance-claim is a **cheap trigger** that *proposes* completion; the grounded
    done-gate **verifies** it. Not-achieved → dispatch another "advance the goal / maintain
    `PLAN.md`" session next cadence (a ralph-loop bounded by the no-progress watchdog + the
    per-item circuit breaker). Session `BLOCKED`/partial → skip the (expensive) done-gate,
    keep advancing. This is trust-in-verify-out precisely: trust the worker to advance and
    to *raise its hand*; never trust its done-claim — the grounded gate is authoritative
    (#358). The session-DONE trigger is a cheap gate ON the expensive gate, not a substitute
    for it.
  - *Landing shape:* P3 ships the mechanical long_lived advance path **flag-gated + additive**
    — planner stays the default fallback until the box `measure_passrate` re-run proves the
    new path holds the 2/2 baseline; only then flip the default and delete `planner.py` +
    `goal-planner.md`. (Migration-order gate O3: the P2 `PLAN.md` skill — #462 — must land first.)
- **[OPEN O5] Firming's fate — RESOLVED in direction (Denys, 2026-08-03), sizing deferred
  to P4.** Firming (516 py) is the clearest instance of the §3a disease: its up-front
  interrogation (`scope_grill`, `answer_unknowns` — a barrage of questions pushed at the
  human before any work) is **distrust-the-input ceremony**, and it goes. What survives is
  **thin**: the human↔goal agreement on the **destination** — the `done_when` the surviving
  done-gate needs as its yardstick (per O4). Everything else that firming used to grill out
  of the human is **pulled by the worker**: it reads the repo, and `PLAN.md`'s
  open-questions section is where genuine unknowns get resolved by investigation, not by
  interrogating the owner first. Net: firming is a **cut** of the interrogation, a **keep**
  of the thin destination-agreement — the push→pull principle applied to the one caller that
  is a human boundary rather than an LLM-second-guess. **Direction locked; the P4 tranche
  sizes the thin-firming surface (what minimal destination-agreement stays, and where).**
- **[OPEN O6] The three gates: advisory or away?** Advisory keeps them as a cheap
  ship-and-flag net (~0 LOC deleted); "away" buys ~600 py. Trust-dial decision (ADR 0007).
- **[OPEN O7] Decomposer OOM — cure or relocate?** The decomposer is the whole-goal
  one-shot OOM fish (`DECOMPOSER_TIMEOUT_MS=300_000`). Does moving decomposition into the
  worker session actually *cure* the host-memory pressure, or relocate it into the sandbox
  (arguably safer — the sandbox owns its own memory)? Must be answered before P4 claims an
  OOM win it didn't earn.
- **[O8] Steer latency — RESOLVED by the pivot.** Steering stays the existing `steer_goal`
  input (no issue-comment handle); the worker reads it at session start and re-plans
  (updating `PLAN.md`). Next-session application is the accepted, durable-not-real-time
  contract — unchanged from today; `steer_goal` does not interrupt an in-flight session.

## 7. What this proposal explicitly does NOT do

- It does not weaken any always-hard gate. Verify, test-integrity, and the done-gate stay
  fail-CLOSED (#186). An unreviewable change still fails closed-and-fast.
- It does not remove the heartbeat, the pause-and-resume machinery, or the block-and-ping
  brakes. The loop still survives crashes, quota pauses, and auth failures unattended.
- It does not add a tick-path LLM call, or any tick-path plan read. The planner cut
  *removes* an LLM call; the thin path is mechanical dispatch + done-gate, nothing read back.
- It does not install third-party skills into the sandbox. The plan-as-durable-doc pattern
  (wayfinder-inspired) is re-expressed as a plain model-agnostic `PLAN.md` worker skill.
