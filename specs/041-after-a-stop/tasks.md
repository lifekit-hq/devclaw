# Tasks: After a stop

## US1 — a Decision is executed by the next tick (P1)

- [X] T001 `decisions.py`: `pending_since(rows, last_plan_at_iso)`, `accepted_close(rows)`; label for `continue`
- [X] T002 `tick.py`: pending Decisions are work; the closed-issue shortcut fires only with nothing to dispatch; the accept-close branch before the work gate; settled-ok routes to the close while an accept stands; defaulted `cancel` executes
- [X] T003 `tick_donegate.py`: `_finalize_accepted_close` — rollup (red ⇒ steer; pending ⇒ ci hold; green ⇒ merge-on-close + ACHIEVE), zero cognition
- [X] T004 `service.py`: `resolve_problem` with option `cancel` fires CANCEL in the same transaction
- [X] T005 `problems.py`: ACCEPT_CLOSE consequence text
- [X] T006 tests: `test_owner_accept_close_closes_without_evaluation` (zero-token + brake), `test_closed_contract_with_a_pending_correction_dispatches` (brake), `test_decision_is_work_next_tick_dispatches`, `test_decide_cancel_cancels`; existing `test_defaulted_accept_and_close_never_emits_achieve` stays green
- [X] T007 constitution V amendment (2.10.0); CLAUDE.md / architecture.md / INDEX.md

## US2 — a stop without a decision never waits for a human (P2)

- [ ] T008 `problems.py`: `CONTINUE`; `tick_dispatch.py`: cap raises the Problem (default continue; repeat cap ⇒ no default)
- [ ] T009 `tick.py` + `tick_donegate._live_contract`: fetch failure ⇒ `mechanical:prep`, TASK-level notify
- [ ] T010 tests: `test_dispatch_cap_blocks_runaway` extended (Problem, default), `test_dispatch_cap_continues_once_then_waits_for_a_decide`, `test_contract_fetch_error_holds_self_healing` (renamed)
- [ ] T011 docs: CLAUDE.md mechanical-blocks paragraph, architecture auto-heal paragraph, INDEX
