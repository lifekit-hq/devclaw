# devclaw Constitution

Seeded 2026-08-13 from the load-bearing invariants in `CLAUDE.md` and
`docs/architecture.md`. Those documents remain canonical — on any conflict,
`CLAUDE.md` wins and this file must be corrected in the same PR. This
constitution exists so the speckit pipeline checks devclaw's *actual* rules
instead of a generic product-feature template.

## Core Principles

### I. OAuth only — never metered billing
Cognition is always `claude` over Pro/Max OAuth. `ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` are actively stripped at every spawn site. No spec may
introduce a path that lets a stray key silently switch autonomous runs onto
metered billing.

### II. Model-agnostic worker layer
Skills are plain markdown; hooks are bash `.sh` files; cross-tool capability
goes through MCP, not vendor tool-wiring. Swapping `claude-code` for another
ACP-speaking agent must only change the runner's agent-drive seam — the
payload/env-selectable agent command (`acp_command` / `DEVCLAW_ACP_COMMAND`)
its ACP client spawns — and the seam stays continuously test-enforced (the
fake-agent regressions, spec 011).

Worker-kind instructions have exactly ONE home: `runner/skills/`. A second
copy is not a fallback, it is a silent fork — an edit lands in the copy
production never reads while the canonical skill says something else, and no
test can tell them apart. A missing bundle fails loud; it never substitutes
text for a worker that then runs unattended (spec-less demolition, #613).

### III. Zero-token idle
An idle goal and an in-flight-still-running goal cost ~0 `claude` calls.
Cheap SQLite/timestamp checks run before any LLM call. A spec that adds
tick-path cognition firing on idle is unconstitutional on its face.

### IV. Single writer to state
Only the TaskQueue mutates task rows; goal state is owned by `GoalStore`
behind CAS'd transitions (`TransitionConflict` over silent clobber).
Markdown status files are generated views, never read back for decisions.

### V. Verification fails closed; "done" is a proposal
A quality-gate crash is not an approval. Completion is gated on grounded
evaluation (`review_repository` against the firmed `done_when`), never on
counting PRs or backlog items. Fail-closed-on-crash governs every gate that is
*consulted*: a gate that runs and cannot produce a verdict never ships on its
own silence (#186). The strictness dial sets which gates are *consulted*, not
just their consequence: under `trust` (the default) the per-increment
adversarial diff review is **not part of the task gate chain at all** — the
goal-level done-gate's grounded evaluation is the close-and-merge authority
(spec 025 merge-on-close: an achieved goal's cumulative PR squash-merges at
the close, and a goal that cannot merge does not close), the done-gate
re-catches the review gate's findings, and — since spec 032 — **the
project's own verification environment is the verdict of record**: the
delivered PR's CI rollup is read as a mechanical fact for the exact head
before any review or evaluator runs and again before the merge; the
validation lane (spec 015) is the backstop; the human is not a stage —
while under `strict` it is consulted and fail-closed exactly as before. A gate
that is by policy not consulted produces no silence to ship on, so removing it
from the `trust` chain does not repeal #186. The verify, test-integrity, and
goal-level done gates stay always-hard in BOTH modes; the browser-E2E gate
stays dial-able (advise-under-trust / block-under-strict) per ADR 0007.
Since spec 041 (2026-09-08) there is exactly ONE human exception to the
evaluator's authority, at the same seam: the owner's explicit `accept_close`
Decision closes the goal on the mechanical facts alone — green CI on the
current head, merge-on-close — with no further evaluation, the reported gap
logged as a follow-up. The owner is the one party who may accept a gap the
evaluator reported; a *defaulted* accept never is, and the evaluator remains
the only machine emitter of `done`. Every other Decision is work the next
tick executes (a correction dispatches; a cancel cancels) — a recorded
decision that waits for a cadence or a human is the defect spec 041 removed.

### VI. Loud failure over silent degradation
Broken delivery fails; lost/corrupt state blocks legibly with an owner ping;
usage limits pause-and-resume with WIP preserved. Any bounded coverage
(top-N, sampling, truncation) says so out loud.

### VII. Fix the class, not the instance
A concrete failure is an instance of a class; change the rule, not the case
that hurt today. Domain specifics (code, PRs, Playwright) stay at the edges
so layers 1–4 remain domain-agnostic.

### VIII. Cognitive guardrails are shed, structural invariants are kept
A mechanism that does thinking the model should do — a classifier, a
heuristic gate, a prompt-scaffold that compensates for the model — is a shed
candidate and must say so in its plan, naming the A/B seam that turns it off
and the instrument that measures it. A structural invariant (fail-closed
verification, test-integrity, the OAuth strip, single-writer/CAS,
done-is-a-proposal, the brakes) is never shed and never A/B-tested for
shedding. The split and the 2×2 shed rule are ADR 0004
(`docs/decisions/0004-eval-workbench.md`, 2026-07-20); this principle makes
them a plan-time Constitution Check gate rather than a retrospective audit.

### IX. Instruct thin, verify thick, verify mechanically
Software owns exactly five things: safety (the sandbox, the OAuth strip),
money (tokens, pauses), state (single writer, CAS), the verdict of record
(the project's CI, the materialize span), and the protocol (what goes into
the worker and what comes out). The agent owns everything that varies per
repo — how to build, how to test, what a good change looks like, how to read
an issue; devclaw never encodes knowledge about a project's code, it supplies
facts and tools and reads the mechanical verdict. A gap is closed in this
order: a missing fact (supplied through the environment or a tool), then a
missing instruction (one line in a skill, checked by an eval), then a
software brake — and a brake only inside the five domains. Python landing
outside them is a smell the plan must argue for by name. The price is
accepted: the agent's output is not predictable and is not made so; the
harness's behaviour is, and verification is thick and mechanical. Standard
practice is adopted, not re-invented: a devclaw-specific mechanism needs a
reason the standard one cannot give. (Ruled by Denys 2026-09-06; spec 032's
CI-is-the-verdict is this principle applied once.)

## Development Workflow

- EVERY spec and tinyspec is admitted against the north star before anything
  else is written (ruled by Denys 2026-09-07): it states which of the three
  north-star failures it moves — *stopped when it shouldn't*, *ran and
  produced garbage*, *ran but needed the owner* — the number on the loop-health
  or scorecard read that will show it, and the condition under which it is
  cut. A change that cannot name one is not written. A story whose only case
  is saving a round, adding a protocol, or completing a spec's shape is
  dropped first, not built last. The north star: devclaw runs 24/7 non-idle
  on planned work, nights come out clean, and it self-heals through stops
  instead of waiting for the owner.
- ALL behavior-changing work starts with the speckit pipeline —
  `/speckit-specify` → `/speckit-clarify` (run WITH Denys, one question at a
  time) → `/speckit-plan` → `/speckit-tasks` → implement
  (see `.claude/rules/speckit-workflow.md`). No implementation before the
  clarify step is done. The spec records rejected alternatives — it is the
  direction memory; the retired proposal→ADR pipeline's artifacts live in
  git history (`git log -- docs/decisions docs/proposals`); ADR 0004 stays.
- The test suite is a tripwire net, not a coverage instrument (ruled by Denys
  2026-08-29): a PR ships a test only when it touches an autonomous-operation
  invariant (zero-token idle, fail-closed gates, CAS/single-writer, OAuth
  strip/fence, pause-and-resume brakes, the materialize span, doctor
  seeded-faults, structural guards); ordinary behavior changes ship no test.
  Zero-token guard tests (`FakeClaude.calls == 0`) are load-bearing — if one
  fails, the change is wrong, never the test.
- Branch per change; squash merges; docs made stale by a diff are fixed in
  the same PR.
- Slice novel work into independently-shippable P1/P2/P3 increments so each
  lands as one reviewable PR. The increment is the unit of REVIEW, not of
  commitment: the whole spec is the commitment, and P1 landing is not a
  stopping point. A goal driving spec work carries a `done_when` covering the
  WHOLE spec. Stories that are genuinely dropped are said out loud; a spec left
  marked "SPECIFIED, NOT IMPLEMENTED" is unfinished work with no owner.

## Governance

This constitution is the invariant statement the speckit pipeline checks
specs against — the enforcement surface for dev work on this repo (ruled by
Denys 2026-08-13, retiring the proposal→ADR pipeline and the invariant-guard
agent). `CLAUDE.md` remains the repo contract: on any conflict `CLAUDE.md`
wins and this file is corrected in the same PR. A spec that requires an
invariant change must say so explicitly and amend this constitution in the
same arc — never silently.

**Version**: 2.10.0 | **Ratified**: 2026-08-13 | **Last Amended**: 2026-09-08
(2.10.0 — Principle V: the owner's explicit `accept_close` closes on mechanical
facts with no further evaluation, and every Decision is executed by the next
tick; spec 041, ruled by Denys 2026-09-08 after fs-318/421/429 cycled
decide → gate → downgrade → Problem → decide and fs-431's recorded correction
never dispatched. Prior: 2.9.0 — Development Workflow gains the north-star admission clause: every
spec and tinyspec names the failure it moves, the number that shows it, and
its cut condition; a mandatory `North-star case` section lands in the spec
template and the tinyspec skeleton. Ruled by Denys 2026-09-07 against
"infinite engineering" — devclaw was halved once for adopted code it never
needed. Prior: 2.8.0 — Principle IX added: instruct thin, verify thick, verify mechanically —
the five software domains, the fact → instruction → brake order, standards
over bespoke mechanisms; 2.7.0 — Principle VIII added: cognitive guardrails are shed, structural
invariants are kept, and every plan answers which it adds. Ruled by Denys
2026-09-04 on the devclaw design audit (a firstmate scout's evidence check),
on the finding that ADR 0004's shed rule had no enforcement point while 32
specs landed in 21 days. Prior: 2.6.0 — Principle V amended for spec 032 verification ownership: "the human
reviews merged work post-merge, revert is the remedy" is replaced by "the
project's own verification environment is the verdict of record; the
validation lane is the backstop; the human is not a stage". Ruled by Denys
2026-09-03 on the audit of 25 achieved goals — devclaw is Claude that works
without him, and a human stage written into doctrine is a missing pipeline
stage. Prior: 2.5.0 — Principle V's trust rationale updated for spec 025 merge-on-close:
"the human reviews every PR" (pre-merge) becomes "the done-gate is the
close-and-merge authority; the human reviews merged work post-merge, revert
is the remedy". Ruled by Denys 2026-08-29 for unattended operation — the #641
merge doctrine is reversed at exactly one seam, the confirmed-achieved close;
nothing merges mid-flight (#486 intact) and nothing else on the settle path
may merge. Prior: 2.4.0 — the Development Workflow slicing clause: an increment is the unit of
REVIEW, not of commitment; the whole spec is the commitment and P1 landing is
not a stopping point. Ruled by Denys 2026-08-22 on the evidence that specs 007,
008, 010 and 012 each stopped after their first story with nothing tracking the
remainder — the old clause said "firm and size only P1 … leave P2/P3
named-unsized until P1 lands", which suspended a decision and named no
condition that restarts it. Prior: 2.3.0 — Principle II gains the one-home rule for worker-kind instructions.
Earned, not theoretical: three copies of the onboard prompt existed with no
discriminator, and PR #610 edited the two production never reads while the
canonical skill already said the same thing. Collapsed by #613. Prior: 2.2.0,
2026-08-19 — Principle II's swap seam named abstractly instead of the deleted
OpenHands `ACPAgent` symbol, spec `011-acp-runner-swap`. Prior: 2.1.0,
2026-08-14 — Principle V strictness-dial consultation semantics, spec
`001-review-gate-repositioning`.)
