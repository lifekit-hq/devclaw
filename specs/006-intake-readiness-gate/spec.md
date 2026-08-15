# Feature Specification: Intake Readiness Gate

**Feature Branch**: `feat/intake-readiness-gate`

**Created**: 2026-08-15

**Status**: Draft

**Input**: User description: "Intake readiness gate for file_intake (P1 of the autonomous issue-driven pipeline arc) — a validator that grades each incoming ask as dispatch-ready or needs-refinement at intake time, before it can be picked up for execution, keeping ungroundable work out of the plan. Intake-only: no autonomy, no tick changes, no auto-dispatch."

## Context & Motivation *(informative — not a spec section, orients the reader)*

`file_intake` is devclaw's single intake doorway: every ask (human or agent) becomes a labeled GitHub issue and returns the issue URL as a receipt. Today the only gate is structural (a required one-paragraph `what`, a `done_when` of at least 20 characters, and stamped provenance). Nothing checks whether the ask is actually *actionable* — a vague or ungroundable ask becomes an issue that looks just as dispatch-ready as a well-scoped one.

A 2026-08-15 prior-art scan found that **no** productized coding-agent system (GitHub Copilot coding agent, AWS Kiro, Devin, Codegen, Sweep, OpenHands resolver) gates issue readiness before planning — they attempt whatever they are handed. At scale this produced GitHub's agent-PR slop problem (agent PRs ~17M/month, an estimated ~1-in-10 legitimate, a platform "kill switch," and a maintainer revolt over untraceable auto-generated work). The readiness gate is therefore the piece the industry skipped, and it is the foundation that must exist *before* devclaw turns on any autonomous execution.

This spec covers **P1 only**: make asks *gradeable*. Autonomy (the tick claiming ready issues, auto-dispatch, the provenance wall, and speckit execution) is explicitly deferred to later slices so the strictness machinery lands before any autonomy flag does.

## Clarifications

### Session 2026-08-15

- Q: When should the readiness grade be computed relative to the intake receipt? → A: **Async** — intake creates the issue and returns the receipt immediately; the grade lands moments later as a label. While the grade is pending, the ask is treated as **not ready**. Filing never blocks on cognition availability.
- Q: How many outcome states should the readiness grade have? → A: **Binary** — `devclaw-ready` or `needs-refinement` only. Fail-closed already means "when in doubt → needs-refinement"; a borderline ask simply lands in `needs-refinement` until sharpened. No third human-review bucket.
- Q: When a `needs-refinement` ask is amended to be groundable, how does it get re-graded? → A: **Manual re-trigger** — re-grading runs on an explicit operator/asker action against that issue; no automatic watching of issue edits, no cognition spend on trivial edits.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An ungroundable ask is caught at the door (Priority: P1)

An asker (a human via chat/Telegram, or an agent) files an ask through the intake doorway. If the ask can be grounded against the target repository — it points at a locatable surface, a concrete change, and a verifiable intent — it lands ready for a human to dispatch. If it cannot be grounded (too vague, names nothing that exists, no discernible change), it lands flagged for refinement, with the receipt telling the asker exactly what is missing. Either way the ask becomes a durable, visible issue — nothing is silently dropped and nothing garbage is silently made to look dispatchable.

**Why this priority**: This is the entire value of the slice — the strictness net that keeps ungroundable work out of the dispatchable backlog. Without it, P1 delivers nothing.

**Independent Test**: File one clearly groundable ask and one clearly ungroundable ask against a real repository; verify the first is marked ready and the second is marked needs-refinement with an actionable reason, and that both produced an issue + receipt.

**Acceptance Scenarios**:

1. **Given** a target repo and an ask that names a locatable surface, a concrete change, and a verifiable intent, **When** it is filed through intake, **Then** an issue is created, the ask is marked `devclaw-ready`, and the receipt reflects the ready state.
2. **Given** a target repo and an ask that is well-formed but names nothing locatable / no concrete change, **When** it is filed through intake, **Then** an issue is created, the ask is marked `needs-refinement`, and the receipt names at least one concrete missing element.
3. **Given** any ask, **When** intake completes, **Then** a durable issue and a receipt URL always exist regardless of the readiness outcome.

---

### User Story 2 - Uncertainty fails closed (Priority: P2)

When the readiness evaluation cannot produce a confident ready verdict — the evaluator errors, returns unusable output, the repository context cannot be gathered, or cognition is unavailable (e.g. a usage-limit pause) — the ask is treated as **not ready**, never optimistically marked ready. It waits in a not-ready state and can be re-evaluated later; it is never allowed to masquerade as dispatchable.

**Why this priority**: Fail-closed is the invariant that makes the gate trustworthy. A gate that opens on its own failure is worse than no gate, because it launders garbage as "graded ready."

**Independent Test**: Force the evaluator to error / return malformed output / receive no repo context; verify the ask is marked not-ready (never `devclaw-ready`) and carries a reason.

**Acceptance Scenarios**:

1. **Given** an ask whose readiness evaluation raises an error or returns unusable output, **When** intake processes it, **Then** the ask is marked `needs-refinement` (not `devclaw-ready`) with a reason indicating the evaluation could not complete.
2. **Given** an ask filed while cognition is paused by a usage limit, **When** intake processes it, **Then** the ask is not marked `devclaw-ready`, and it becomes eligible for readiness evaluation once cognition resumes.
3. **Given** a target repo whose context cannot be gathered, **When** an ask against it is evaluated, **Then** repo facts are treated as unknown and the ask is not marked `devclaw-ready`.

---

### User Story 3 - The refinement reason is actionable (Priority: P3)

An asker whose ask was flagged `needs-refinement` can read, from the issue/receipt, the specific missing element(s) — e.g. "no locatable surface named," "no concrete change described," "referenced component not found in the repo" — so they can amend the ask and have it re-graded, rather than being told only "not ready."

**Why this priority**: Legibility turns the gate from a wall into a conversation. It is valuable but the gate is still correct (fails safe) without the detailed reason, so it ranks below the core grade and the fail-closed guarantee.

**Independent Test**: File a not-ready ask; verify the surfaced reason names at least one concrete, fixable element; amend the ask to be groundable and verify it can be re-graded to `devclaw-ready`.

**Acceptance Scenarios**:

1. **Given** an ask marked `needs-refinement`, **When** the asker views the issue/receipt, **Then** at least one concrete missing element is stated.
2. **Given** a `needs-refinement` ask that is edited to name a locatable surface and concrete change, **When** it is re-evaluated, **Then** it can be marked `devclaw-ready` and the `needs-refinement` state is cleared.

---

### Edge Cases

- **No repository context available** (best-effort snapshot returns nothing): repo facts are unknown → the ask cannot be confidently grounded → not ready (fail-closed), with a reason distinguishing "couldn't read the repo" from "ask is vague."
- **Cognition unavailable / usage-limit pause**: readiness evaluation is deferred; the ask sits not-ready (not silently ready) and is re-evaluated on resume. Filing itself must not be blocked by the pause — the receipt is still returned.
- **Ask references a surface that does not exist in the repo**: ungroundable → needs-refinement, reason names the missing surface.
- **Agent-filed vs human-filed ask**: treated identically in P1 (uniform grading). Provenance-differentiated handling is out of scope (see Out of Scope).
- **Re-filing / duplicate asks**: no dedup in P1 (each ask is graded on its own). Deduplication is out of scope.
- **An ask that is groundable but large/multi-surface**: still gradeable as ready in P1 — readiness judges "scoped enough to attempt firming," not size/sliceability (that is firming/decompose's job).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every intake ask MUST result in a durable issue and a returned receipt URL, regardless of the readiness outcome. No ask is ever dropped.
- **FR-002**: The system MUST evaluate each intake ask for readiness, defined as whether it can be **grounded against the target repository** — it identifies (or clearly implies) a locatable surface, a concrete change, and a verifiable intent.
- **FR-003**: An ask that is confidently groundable MUST be recorded as `devclaw-ready`.
- **FR-004**: An ask that is not confidently groundable MUST be recorded as `needs-refinement` and MUST surface at least one concrete missing element.
- **FR-005**: The readiness evaluation MUST fail closed: any condition that prevents a confident ready verdict (evaluator error, unusable/malformed output, missing repository context, cognition unavailable) MUST result in **not ready**, never ready.
- **FR-006**: Readiness MUST be judged **only** on "is this scoped enough to attempt firming." It MUST NOT derive completion criteria (`done_when`) or a task checklist — those remain the firming phase's responsibility. The intake-readiness gate and the firming gate MUST NOT overlap in what they decide.
- **FR-007**: The readiness outcome MUST be recorded as the source-of-truth state on the issue, visible to humans and to future automation, via a `devclaw-ready` / `needs-refinement` label. Generated human views may mirror it but MUST NOT be the source of truth.
- **FR-008**: Repository grounding MUST NOT infer facts from the host process, the agent's working directory, or any other repository; absent repository context ⇒ unknown ⇒ not confidently ready.
- **FR-009**: The gate MUST NOT auto-dispatch work and MUST NOT change heartbeat/tick behavior or its token cost. Dispatch remains on the existing human-invoked path. The zero-token idle guarantee MUST remain intact (no new idle-tick cognition or subprocess).
- **FR-010**: A `needs-refinement` ask that is subsequently amended to be groundable MUST be re-gradable to `devclaw-ready` via an **explicit re-trigger** (operator/asker action on that issue). The system MUST NOT automatically watch for or re-grade on issue edits.
- **FR-011**: Filing MUST NOT be blocked by cognition unavailability — the receipt is always returned; only the readiness grade may be deferred.

### Key Entities *(include if feature involves data)*

- **Intake Ask**: the incoming request — `what`, `done_when`, `asker`, `channel`, optional `context`, provenance, timestamp. (Existing.)
- **Readiness Verdict**: the outcome of evaluation — ready / not-ready, plus, when not-ready, one or more concrete missing elements (the reason). Ephemeral input to the label decision; not a new durable store.
- **Readiness Label**: the durable source-of-truth state on the issue (`devclaw-ready` or `needs-refinement`), alongside the existing intake label.
- **Repository Context Snapshot**: best-effort, read-only facts about the target repo used to ground the evaluation; unavailable ⇒ unknown.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of intake asks terminate with both an issue and a readiness label — no ask is left dropped or in a permanent unlabeled limbo.
- **SC-002**: 0% false-ready on failure paths — no ask is ever labeled `devclaw-ready` when the readiness evaluation did not complete successfully (measured across induced error / malformed-output / no-context / paused-cognition cases).
- **SC-003**: An ask that names no locatable surface and no concrete change is graded `needs-refinement` (not ready) in 100% of the regression cases.
- **SC-004**: 100% of `needs-refinement` outcomes carry at least one concrete, asker-actionable missing element in the surfaced reason.
- **SC-005**: Idle-tick token cost is unchanged from before the feature (the zero-token idle guard test count stays at 0 cognition calls on idle/blocked paths).
- **SC-006**: A `needs-refinement` ask amended to be groundable can be re-graded to `devclaw-ready` without re-filing a new ask.

## Assumptions

These are decisions resolved during the 2026-08-15 clarify session (see Clarifications) plus informed defaults for details the description left open.

- **Timing — async grade (resolved).** Readiness is evaluated **after** the issue is created (fast receipt); the issue lands in a "pending / not-yet-ready" state **treated as not ready** until the grade resolves. Filing is never blocked by cognition availability.
- **Grade granularity — binary (resolved).** The verdict is exactly `devclaw-ready` or `needs-refinement`. No third "borderline / human-review" bucket; fail-closed sends any uncertain ask to `needs-refinement`.
- **Re-grade trigger — manual (resolved).** A `needs-refinement` ask is re-evaluated only on an explicit operator/asker re-trigger against that issue — no automatic watching of issue edits.
- Readiness is recorded as GitHub labels on the target project's repo; the existing intake label is retained.
- The gate applies **uniformly** to human- and agent-filed asks in P1. Provenance-differentiated treatment (the wall between self-filed and external asks) is deferred to the autonomy slice.
- Repository context is gathered by a **best-effort, never-raising** snapshot collector; a git/network hiccup degrades to "no context" (fail-closed), it does not fail intake.
- Cognition is OAuth-only, one-shot, and subject to the existing account-wide usage-limit pause; a paused evaluation defers the grade and re-runs on resume.
- The evaluation is a new cognition caller distinct from firming; it consumes the ask + repo context and returns a readiness verdict only (no `done_when`, no checklist).

## Out of Scope *(P1 boundary — named for later slices)*

- **Tick claiming ready issues** — the heartbeat scanning `devclaw-ready` issues and picking one up. (P2.)
- **Autonomous dispatch + scorecard-gated activation flag** — turning the crank without a human. (P2.)
- **The provenance wall** — barring a devclaw-self-filed ask from flowing into the execute queue without independent promotion. (P2, ships with autonomy.)
- **Speckit execution binding** — issue → spec → tasks → PR → close. (P3.)
- **Async clarify conversation** — devclaw posting a clarifying question as an issue comment and waiting, instead of a one-shot needs-refinement grade. (Later.)
- **Deduplication / conflict detection** across ready asks. (Later; the D-bucket mechanical governors.)

## Rejected Alternatives *(direction memory — do not re-litigate without new evidence)*

- **(a) Keep only a structural/length gate** (require longer `done_when`, more fields). **Rejected**: a well-formed but ungroundable ask still passes and looks dispatch-ready — length is not groundability. This is the gap that motivates the feature.
- **(b) No gate — attempt everything** (the OSS/productized default: Copilot, Kiro, Devin, Sweep, OpenHands resolver). **Rejected**: this is the documented slop failure mode at scale (~17M agent PRs/month, ~1-in-10 legit, platform kill switch). Attempting garbage is the exact outcome the gate exists to prevent.
- **(c) Gate at dispatch time instead of intake.** **Rejected**: we want garbage visible and labeled **at the door**, not discovered when execution is already attempted. Grading at intake makes the dispatchable backlog trustworthy by construction and keeps the failure loud and early.

## Constitution Alignment *(no amendment required)*

This feature upholds existing invariants and requires no constitution change:
- **Fail-closed verification** — the gate never opens on its own failure (FR-005).
- **OAuth-only cognition** — the evaluator is a one-shot OAuth `claude` call; no API key path.
- **Zero-token idle guard** — evaluation happens on the intake path, never added to the idle tick (FR-009).
- **Single-writer / source-of-truth state** — the GitHub label is the durable truth; human views mirror it (FR-007), consistent with "generated views are never read back for decisions."
- **Loud failure over silent degradation** — garbage becomes a visible, labeled, un-actioned issue rather than a silent pass.
