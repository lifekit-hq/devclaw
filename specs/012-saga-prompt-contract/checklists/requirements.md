# Specification Quality Checklist: Saga & Unit-of-Work Prompt Contract

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-22
**Clarified**: 2026-08-22 (with Denys, one question at a time)
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

## Clarifications resolved (2026-08-22, with the owner)

1. **Does a single-unit work item skip the completion judgement?** → **No.**
   Everything remains a saga whatever its size; a work item records an *expected
   increment count*, never a saga-or-task verdict. Evidence: a single-unit work
   item passed its mechanical verification while materially incomplete — the
   feature was unwired but nothing deleted, justified by a gate that did not
   exist — and only the saga-level judgement caught it. Encoded as FR-012,
   FR-012a, SC-005a.

2. **Is the saga framing re-sent per increment or referenced?** → **Re-sent in
   full.** A fresh sandbox has no memory, so referencing would depend on the
   worker following a pointer; input must be expected, not hopeful. The size
   objection is an argument for compact framing (US2), not for fetchable framing.
   Encoded as FR-009a, FR-009b, and the US2 interlock note. Note this is already
   today's behaviour — what was missing is the bound, not the mechanism.

3. **Who determines the expected increment count?** → **The filer claims it;
   grading validates and never silently overwrites.** A grader-judged number would
   drift between identical re-grades, defeating predictability; a filer-only number
   has no check. Disagreement is recorded and surfaced to a human. Encoded as
   FR-010, FR-010a, FR-010b, FR-011, SC-005b.

## Notes

Naming, carried in the spec header rather than left implicit: this is **012**, not
the "010" named in the 2026-08-18 ruling. 010 was never allocated; 011 took the
next slot on 2026-08-19 and the allocator assigns `highest + 1`.

Deliberate scope decisions to re-read before planning:

- **US4 (planned parallelism) is named and unsized on purpose.** It is the scaling
  story, but it is meaningless until the single-increment path is predictable.
  Sizing it now would violate the slice-don't-estimate rule.
- **FR-009 guards this spec against its own failure mode.** A schema whose slots
  nobody acts on makes prompts longer and worse on the call class most prone to
  failure. Every proposed slot must be justified by the behaviour it changes — and
  FR-009a multiplies that cost by the increment count, so the bar is higher, not
  lower.
- The terminology table is adopted verbatim from the 2026-08-18 ruling and is not
  open for re-derivation.

**Status**: ready for `/speckit-plan`.
