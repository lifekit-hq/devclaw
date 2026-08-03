# Proposal — cognition demolition: collapse 8 control-plane cognition boundaries to 1, migrate durable plan-state to a worker-owned wayfinder map on the target repo

- **Status:** **P1 LOCKED (direction) — 2026-08-03**, rest DRAFT. P1's only mandatory
  `[OPEN]` (O1) resolved at grounding (the no-progress watchdog is already mechanical);
  P1 scope firmed against the real code (`_run_mid_flight_eval` call-site removal). Denys
  authorized proceeding to implementation. The P2/P3/P4 arc stays DRAFT with O2–O8 open
  (O5 firming-fate + O6 gates advisory/away are Denys's calls, deferred to their slices).
  Captured from a multi-message brainstorm
  (vault `~/memory/projects/devclaw/demolition-spine-2026-08-03.md`). The
  **architecture forks are already resolved** by Denys in that conversation and
  recorded in §3 (plan-state home, control-plane read-only, full wayfinder issue
  machinery, map always on the target repo). What remains before LOCK is the
  **implementation clarify step** — the 8 `[OPEN]`s in §6. **No code before lock**
  (this is the largest direction change since the v1→v2 question; it amends the
  control-plane planning spine of [ADR 0003] and the single-writer-to-plan-state
  invariant — see Invariant impact).
- **Invariant impact:** **HIGH, and named as a headline, not a footnote.**
  1. **Single writer to state — amended for *plan* state, unchanged for *lifecycle*
     state.** Today the goal's plan ("what's next") is re-derived each tick by a
     control-plane LLM (the planner) and persisted goal-side (`GoalStore`). This
     proposal moves **plan-state** to a GitHub-issue *map on the target repo,
     written solely by the worker*; the control plane becomes a **reader/renderer**
     of it and never re-plans. Goal **lifecycle** state (investigating/executing/
     blocked/done) stays owned by `GoalStore` under the CAS'd `transition()` choke
     point — unchanged. The split is deliberate: cognition → worker, consumption →
     mechanism.
  2. **Zero-token idle guard — preserved.** Reading the map is `gh` (network, no
     `claude` tokens). But it is a *subprocess*, so it runs only for **executing**
     goals, gated AFTER the `should_plan`/phase checks, and prefers event-driven
     wake over blind per-tick polling (`[OPEN] O2`). The `FakeClaude.calls == 0`
     idle assertions must stay green — the planner-cut REMOVES a tick-path LLM call.
  3. **Model-agnostic worker layer — preserved.** Worker↔tracker is plain `gh`;
     the "how to work a wayfinder map" guidance is plain markdown, NOT the
     mattpocock Claude-Code skill installed into the sandbox (that would also blow
     the curated-allowlist invariant). We borrow the *pattern*, re-expressed.
  4. **"Done is a proposal gated on grounded evaluation" — preserved.** The
     done-gate evaluator is the ONE surviving cognition boundary; it reads the map's
     destination against the repo (`[OPEN] O4`).
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
  - **`console-legibility.md` / trace_view (#453)** — the map-on-a-tracker is the
    ultimate answer to that thread's "the console renders IDs, not what happened":
    the plan-state becomes a shared URL the worker actually writes, watchable and
    steerable directly.
  - **`foundation-checkpoint.md` (#432)** — both read from firming's derived
    `done_when`/asserts; the firming relocation (`[OPEN] O5`) must reconcile with it.
  - **`gap_operator_ux` (vault)** — steering-by-issue-comment largely closes it.

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

**New code back (net-add):** worker reads/renders/walks the map + atomic-write +
corrupt-block ≈ **+200 py**.

**NET ≈ 2,500 LOC deleted** — ~8% of the codebase, **~20% of the `goal/` layer** (the
layer that's *meant* to be thin). The headline is not the Python — it's that **~75% of
the hand-written cognition-prompt corpus evaporates**: the bloat was never the code, it
was the six prompt files written to spoon-feed six separate LLMs. Tick-plumbing
(`tick_context`/`settle`/`guards`/`dispatch`, `service`, `triage`) simplifies as a
secondary effect (unmeasured, real).

**The substrate already exists:** devclaw chose GitHub Issues as its backlog substrate
months ago (Jira rejected) and self-issue-filing is live — so "adopt wayfinder's full
machinery" points existing `gh` plumbing at a new shape rather than building an issue
system. The machinery cost is low *because* the plain-file shortcut was declined earlier.

## 3. The direction — resolved forks (Denys, 2026-08-03)

> **Durable plan-state is a worker-owned wayfinder map — full GitHub-issue machinery —
> that ALWAYS lives on the target repo's tracker. The control plane READS and RENDERS
> it, drives dispatch MECHANICALLY off the frontier, and NEVER re-plans. Steering is a
> comment on the map issue. The planner LLM call dies as a consequence, not a goal.**

- **The map = a `wayfinder:map` parent issue** (index: destination · decisions-so-far ·
  frontier-fog · out-of-scope) **+ child decision tickets** (`wayfinder:<type>` labels:
  research/prototype/grilling/task; blocking relationships; a resolution comment on
  close). The worker claims an unblocked frontier ticket, resolves it, posts the
  resolution, closes it, updates the map body. It is an **index, not a store** — each
  decision lives in exactly one place (its ticket); the map gists and links.
- **The tick becomes mechanical:** read the map (cheap `gh`, cached like the last-SHA
  bookmark) → unblocked frontier ticket? dispatch a worker session to it → all closed?
  propose done → done-gate. No LLM to decide "what's next."
- **Steering survives:** `steer_goal` appends to the map's Notes (an input); the worker
  re-reads and re-plans its frontier next session. Control plane writes an *input*, never
  the *map* — single-writer-to-the-plan holds; `goals-are-durable-no-field-patches` holds.
- **Map always on the target repo — one invariant, no fallback.** Fresh `create_repo`
  goal: devclaw made the repo → issues on by default. Existing repo with push access:
  issues available. Genuine edge (issues disabled / no-issue-write repo): **blocks
  legibly** ("target repo can't host the plan-map — enable issues / point elsewhere"),
  the loud-failure mode, not a silent self-repo reroute.
- **The price, paid with existing patterns:** GitHub is an external dependency (uptime +
  5000/hr rate limit). The control plane **caches the last-read map** (last-SHA-bookmark
  shape); a transient hiccup degrades to last-known-map; a too-stale/unreadable map
  **blocks legibly** (#185/#188). Reads are bounded and gated to executing goals.

## 3a. The principle underneath — trust the input, verify the output (Denys, 2026-08-03)

The LOC count is a symptom; this is the disease. devclaw today **PUSHES** context — big
pre-chewed prompts and up-front interrogation — into every cognition step and every new
worker session, *because it distrusts the agent to find things out for itself*. That
distrust is the source of the ~105 KB prompt bloat, the fat decomposer/planner (OOM
pressure), and the firming grill. The demolition's deepest move is to flip push → **PULL**:

- **Context is pulled, not pushed.** A worker session gets a light, subagent-style brief —
  *goal + the map issue URL + "the repo is yours to read, go"* — and pulls what it needs
  from durable places (the wayfinder map, the repo's committed `AGENTS.md`, the repo
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
| **KEEP (the body)** | sandbox, delivery, console, MCP surface, queue, `loom`, state-store; the **done-gate evaluator** (grounded on the map destination) | trust-neutral; the reason v1 was kept |
| **KEEP as mechanism** | tick/heartbeat loop; no-progress watchdog (fed a **mechanical** signal, `[OPEN] O1`); verify gate; test-integrity gate; block-and-escalate | the four durable-pursuit wires; must survive |
| **DEMOTE** | adversarial-review, browser-E2E, reachability gates → advisory (or away, `[OPEN] O6`) | quality gates, not pursuit; advisory *is* "don't interrupt me" (ADR 0007) |
| **CUT (dissolve into worker)** | planner, decomposer, research, world-research, summarizer; firming (`[OPEN] O5`); progress-half of evaluator | LLM-second-guessing / control-plane re-chewing; the plan-state migration is what makes these safe |

## 5. Sequencing (firm P1 only; name the rest — spec-lifecycle sizing rule)

- **Precondition (not a slice of this proposal):** revive `measure_passrate` as the live
  scoreboard — one baseline run on the box, then the ADR 0006 continuous projection as
  the ongoing number. Without it, every cut is faith-based demolition.
- **P1 (firmed after grounding, ≈ 1 PR):** cut the **per-tick mid-flight evaluator**.
  Grounded scope (2026-08-03): this is `_run_mid_flight_eval` in `goal/tick.py` (L394–441),
  fired every `EVAL_EVERY` deliveries on the **long_lived executing** path (the `one_shot`
  path already runs zero per-tick cognition). Remove the call site + the dead function.
  `evaluator.py` STAYS — it IS the surviving done-gate (`evaluate(at_done_gate=True)`,
  still called from `tick_donegate.py`). No new "watchdog food" needed (O1 resolved).
  **Semantic trade to accept consciously:** removing the mid-flight eval also removes its
  mid-flight `stalled`/`needs_human` LLM-judgment block AND the `off_track` course-correction
  (the 43%-unparseable thrash). Remaining mid-flight brakes: the **zero-token no-progress
  watchdog** (churning-without-shipping), the **done-gate** (not-actually-done), and the
  **per-item circuit breaker**. This is the demolition's intent — retire the cognition
  brake, keep the mechanical ones — but it IS a reduction in mid-flight catch granularity
  (per-tick eval → 6h wall-clock), so it's an explicit owner sign-off, gated at merge on
  the baseline number. Fold in `feat/rc1-ground-done-gate` (ground the done-gate) as a
  companion once P1 lands.
- **P2 (named, unsized):** the **wayfinder-map substrate** on the target repo — worker
  writes it, control plane reads/renders/caches it, done-gate reads its destination,
  corrupt/stale blocks legibly. The migration that must precede the planner cut.
- **P3 (named, unsized):** the **planner cut** — safe once P2 lands; the tick walks the
  map instead of re-deriving next-action.
- **P4 (named, unsized):** **decomposer + firming** relocation, **summarizer** removal,
  the three gates advisory→away. Decomposer is the OOM fish (`[OPEN] O7`) — relocate with
  care, never tranche-1.

## 6. `[OPEN]` — the clarify step (mandatory before LOCK)

The architecture is settled (§3). These are the implementation decisions; each needs an
answer or an explicit deferral-with-owner before the status flips to LOCKED.

- **[OPEN O1] Mechanical watchdog food — RESOLVED at grounding (2026-08-03).** The
  no-progress watchdog (`tick_guards.py:_check_no_progress`) is ALREADY a zero-token
  wall-clock check keyed on `last_progress_at` (bumped on delivery) — it never consumed
  the LLM `off_track`. So cutting the per-tick evaluator does NOT starve the brake; no
  new food is needed for P1. (Pre-map, "a delivery landed" is already the signal; when
  the map lands in P2 it becomes "a frontier ticket closed" — a P2 refinement, not a P1
  blocker.) **RESOLVED — P1 unblocked.**
- **[OPEN O2] Map read cadence + caching seam.** Per-tick bounded `gh` poll vs
  event-driven wake (GitHub webhook / the firstmate-style wake already live). Where does
  the last-read-map cache live (a `goal_docs` projection? the SHA-bookmark table)?
- **[OPEN O3] Migration order is load-bearing.** Confirm P3 (planner cut) can NOT precede
  P2 (map exists) — ripping the planner before the map is the exact "break the thesis in
  the gap" failure. Should be a written gate on the tranche, not just intent.
- **[OPEN O4] Done-gate vs the map destination.** Does firming still set `done_when`, or
  does the map's Destination ticket subsume it? How does the surviving done-gate read the
  map (destination ticket body vs a structured field)? Reconcile with foundation-checkpoint.
- **[OPEN O5] Firming's fate — RESOLVED in direction (Denys, 2026-08-03), sizing deferred
  to P4.** Firming (516 py) is the clearest instance of the §3a disease: its up-front
  interrogation (`scope_grill`, `answer_unknowns` — a barrage of questions pushed at the
  human before any work) is **distrust-the-input ceremony**, and it goes. What survives is
  **thin**: the human↔goal agreement on the **destination** — the `done_when` the surviving
  done-gate needs as its yardstick (and the map's Destination, per O4). Everything else that
  firming used to grill out of the human is **pulled by the worker**: it reads the repo, and
  the map's *frontier-fog* is where genuine unknowns get resolved by investigation, not by
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
- **[OPEN O8] Steer latency.** Steer = comment on the map issue, applied at the worker's
  next session start. Confirm next-session application is acceptable (durable, not
  real-time), and that `steer_goal` does NOT also need to interrupt an in-flight session.

## 7. What this proposal explicitly does NOT do

- It does not weaken any always-hard gate. Verify, test-integrity, and the done-gate stay
  fail-CLOSED (#186). An unreviewable change still fails closed-and-fast.
- It does not remove the heartbeat, the pause-and-resume machinery, or the block-and-ping
  brakes. The loop still survives crashes, quota pauses, and auth failures unattended.
- It does not add a tick-path LLM call. The planner cut *removes* one; the map read is a
  bounded `gh` op gated to executing goals.
- It does not install third-party skills into the sandbox. wayfinder is a *pattern* here,
  re-expressed as plain model-agnostic worker guidance.
