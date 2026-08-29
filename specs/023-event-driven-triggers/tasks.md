# Tasks: Event-Driven Triggers (spec 023)

**Input**: [spec.md](./spec.md), [plan.md](./plan.md). Compact single-PR arc
(the wake-on-event design makes US1+US3 one mechanism); implemented
2026-08-29 in the unattended-week push.

- [X] T001 `DEVCLAW_WEBHOOK_SECRET` in devclaw/config.py + docs/reference/env-vars.md row (unset ⇒ route off)
- [X] T002 Event router devclaw/goal/events.py — wake table (PR merged / issue closed / checks completed → trigger-named goal log + poke), grading on issues opened/edited via the existing intake regrade, unregistered-repo drop with reason (US1+US2+US3, FR-001..FR-009)
- [X] T003 Route devclaw/server/routes/webhooks.py — HMAC SHA-256 verify over the raw body, 404-when-off, 401-on-bad-signature, ping, 202-fast with background routing; registered in http.py BEFORE console (shadow matrix green)
- [X] T004 regrade_intake docstring: issue edits now auto-grade with webhooks on
- [X] T005 docs/runbooks/webhooks.md (Funnel path-scoped exposure per the 2026-08-29 ingress ruling + per-repo gh wiring) + docs/INDEX.md row
- [X] T006 Named regression tests in tests/test_webhook_events.py: test_pr_merged_event_wakes_goals_with_named_trigger, test_pr_closed_without_merge_is_ignored, test_issue_closed_and_check_completed_wake_too, test_issue_opened_and_edited_trigger_grading_at_manual_verb_cost, test_unregistered_repo_is_dropped_with_reason_never_an_error, test_grading_failure_is_loud_but_never_raises, test_duplicate_delivery_is_idempotent, test_route_is_off_without_a_secret, test_route_rejects_bad_signature, test_route_answers_ping_and_accepts_events_fast
- [ ] T007 Live legs at deploy: funnel wiring, 4-repo webhook config, one merged-PR wake observed (SC-001) and one auto-graded issue (SC-002)
