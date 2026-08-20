# Specification Quality Checklist: Speckit-Native Amputation

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-20
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [ ] No [NEEDS CLARIFICATION] markers remain
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

- **Two `[NEEDS CLARIFICATION]` markers remain by design** (FR-017, FR-018),
  both on **US4 (P2)** — neither blocks the P1 amputation. The 2026-08-20
  clarify session deliberately spent its questions on P1 risk (live-DB
  migration, external MCP callers, an unruled cut) rather than on a P2 story
  that will not be implemented in this PR. Assistant recommendations are
  recorded inline for a later pass to accept or overturn.
- **The clarify pass corrected two assistant errors**: the MCP surface is 47
  tools, not 45 (SC-004 and `baseline.md` corrected), and `goal/triage.py` had
  been written into the cut inventory without an owner ruling — it is now
  Deferred on the spec's own test.
- **US4 was demoted from P1 to P2 on evidence**, not preference:
  `donegate_churn` has never visibly fired (0 occurrences across 89 problem
  fingerprints and 19 cycle reports), and FR-016's clause filter captures most
  of the observed benefit without the verdict move or the constitutional
  amendment.
- **Single-PR shape is an owner ruling against the assistant's recommendation.**
  The accepted tradeoff (bisect surface ~6,400 lines, no intermediate green
  commit) and its mitigations (FR-020 inventory, FR-021 recorded baseline, the
  `pre-amputation-v0.3.0` tag) are recorded in the spec's rejected-alternatives
  section.
- **`deploy/` is deferred on evidence**, not cut on a shrug — it fails this
  spec's own "does it exist because there was no speckit?" test and is
  live-wired to both registered projects.
- **"Non-technical stakeholder" reading is adapted to this repo**, matching the
  house style of `specs/011-acp-runner-swap/spec.md`: named code paths appear in
  the informative Context section as evidence, and are kept out of Requirements
  and Success Criteria, which stay behavioral.
- Items marked incomplete require spec updates before `/speckit-plan`.
