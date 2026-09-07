# Specification Quality Checklist: Loop Health Metrics

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-06
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- **Deliberate divergence on "no implementation details"**: the *Why this exists*
  section cites concrete code locations as evidence for the problem statement,
  and FR-002 names the existing `blocked_kind` members verbatim. Both are
  intentional. FR-002's whole point is *reuse this exact vocabulary, do not
  invent a parallel one* — the requirement is unstatable without the names. The
  citations are evidence, not design.
- **Clarify session completed 2026-09-06** — five questions asked and answered
  with the owner, all encoded back into the spec (see `## Clarifications`). The
  two highest-impact outcomes were structural, not cosmetic: the responsibility
  model became three buckets rather than a binary owner/devclaw split, and
  merged-work cost is now segmented by delivery shape so the figure cannot drift
  with the delivery mix.
- **One contradiction was introduced and resolved during clarify**: FR-005b's
  not-stuck rate is a single headline percentage, while Out of Scope rejects a
  single "% non-idle". Both now stand, with the distinction stated explicitly in
  the Out of Scope entry — utilization punishes legitimate idleness, the
  not-stuck rate excludes it by construction.
- **Constitution alignment**: III (zero-token idle) pinned by FR-006 and SC-007;
  IV (single writer) by FR-013; VI (loud failure) by FR-011/FR-020/SC-005;
  IX (judgment belongs to the agent) by the rejected complexity classifier.
- **Carries a stated RISK section**, not an assumption: it is unverified whether
  the worker agent reports usage in production. US4 depends on it; US1–US3 do
  not. Flagged for verification before US4 is planned.
- **Two corrections applied after the first commit** (grounding pass against the
  code, 2026-09-06):
  - US4 was described as building a missing number. It is not — a naive
    `tokens_per_merged_pr` / `cost_per_merged_pr_usd` already exists in
    `compute_scorecard`, blending delivery shapes, charging shipped-nothing
    tokens to the merged denominator, and window-bounded. US4 now states the
    three defects and FR-015a requires REPLACING it rather than adding a second
    disagreeing figure.
  - First-pass rate was presented as a clean quality signal. It may be measuring
    goal SIZE instead: `done_when` covers the whole spec and the worker ships one
    slice per session, so a multi-increment goal's first `done` proposal is
    structurally rejected by design. Recorded as a stated hypothesis with the
    test that settles it, which US6's data supplies.
- **US6 (estimate calibration) was added after the clarify session** and has NOT
  been through clarify. Three decisions in it were taken as stated defaults and
  need the owner's ruling before it is planned: (a) both predictors are recorded
  rather than one, (b) the minimum sample below which no correlation is reported
  is stated but unset, (c) the record is deliberately inert — FR-026 forbids
  acting on it. Everything else in the story follows from those three.
- **A second contradiction was introduced and resolved by US6**: Out of Scope
  rejects a task-complexity classifier, while US6 records a pre-execution size
  estimate. Both stand — the rejected item is *manufacturing* a complexity
  judgment; US6 measures the estimate devclaw already computes and discards, and
  its explicit outcome is a promote-or-delete decision. The distinction is
  stated in the Out of Scope entry itself, matching how the utilization /
  not-stuck contradiction was handled.
- **Constitution alignment for US6**: III (zero-token) by FR-026 — no new model
  call, the estimate is already paid for on an existing one; IV (single writer)
  by FR-022 — extends the existing convergence row at the existing terminal
  transition rather than adding a store or a write point; VI (loud failure) by
  FR-024/FR-025 — an absent prediction and an inadequate sample both read as
  unknown; IX (judgment belongs to the agent) by FR-026 — Python records the
  number, it does not act on it.
