# Specification Quality Checklist: Unit of Work & Planned Parallelism

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-22
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

Three `[NEEDS CLARIFICATION]` markers remain, deliberately. All three are
scope-level decisions with genuinely different consequences and no safe default,
and the repo's workflow requires the clarify round to run **with the owner**, one
question at a time. They are listed under "Outstanding Clarifications" in the spec:

1. Whether single-unit work items route around the saga completion judgement.
   Evidence in Assumptions points to "no" — a single-unit work item passed its
   mechanical verification on 2026-08-22 while being materially incomplete, and
   only the saga-level judgement caught it.
2. Whether saga-level framing is re-sent per unit of work or referenced once.
   This trades prompt size against self-containment on the least reliable call
   class in the system.
3. Whether work-item size is filer-supplied, grader-judged, or claimed and
   validated.

Two naming notes carried in the spec header rather than left implicit:

- This is **012**, not the "010" named in the 2026-08-18 ruling. Number 010 was
  never allocated; 011 took the next slot on 2026-08-19 and the allocator assigns
  `highest + 1`. The alias is recorded in the spec header.
- The terminology section is adopted verbatim from the ruling and is not open for
  re-derivation in this spec.

Deliberate scope decisions worth re-reading before planning:

- **US4 (planned parallelism) is named and unsized on purpose.** It is the scaling
  story, but it is meaningless until the single-increment path is predictable.
  Sizing it now would violate the slice-don't-estimate rule.
- **FR-009 is a guard against this spec's own failure mode.** A schema whose slots
  nobody acts on makes prompts longer and worse on the call class most prone to
  failure. Every proposed slot must be justified by the behaviour it changes.
