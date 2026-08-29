# Tasks: Ticket as Contract (spec 024)

**Input**: [spec.md](./spec.md). Compact arc — most of US1/US3 landed with
spec 022's dispatch_issue (done_when="" → live scenario contract, slots
declared empty, spec slot as grounding only per the ticket-wins ruling).

- [X] T001 Issue-backed create lane needs no slot arguments: admission skips the spec-012 slot rejections when has_issue_refs (devclaw/goal/admission.py); service coalesces omitted slots to declared-empty for storage (devclaw/goal/service.py); create_goal docstring updated (US2, FR-003)
- [X] T002 FR-005: every live-contract gate round logs the judged issue revision (content hash) in devclaw/goal/tick_donegate.py `_live_contract`
- [X] T003 .github/ISSUE_TEMPLATE/devclaw-work.md — What / Acceptance / Out of scope / Invariants / Established (US2)
- [X] T004 Named regression tests: test_issue_backed_goal_needs_no_slot_arguments, test_prose_goal_still_rejects_unfilled_slots (FR-004), test_issue_template_carries_the_saga_sections, test_done_gate_logs_the_judged_issue_revision
- [ ] T005 Template rollout to finance-sentry / lifekit-dashboard / lifekit-common (small PRs, ride today's deploy pass)
