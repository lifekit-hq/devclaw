# Quickstart Validation: Universal Issue Adoption

How to prove the feature works, stubbed and live. Contracts:
[contracts/mcp-tools.md](./contracts/mcp-tools.md) · entities:
[data-model.md](./data-model.md).

## Prerequisites

- Stubbed: `pip install -e ".[dev]"`; run from the feature worktree with the
  import-path check from `.claude/rules/testing.md`.
- Live (optional, `/live-shakedown` territory): a logged-in `gh`, a registered
  project whose repo has a few hand-written open issues (finance-sentry is the
  motivating target).

## Stubbed validation (the gate for the PR)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_intake.py
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q          # full suite — count ≥ baseline
```

Expected: the named regression tests from plan.md all pass, notably

- `test_regrade_adopts_plain_issue_without_what_section`
- `test_regrade_intake_format_issue_behavior_unchanged` (SC-003)
- `test_regrade_rejects_closed_issue_loudly`
- `test_grade_backlog_caps_batch_and_reports_remainder_without_continuing`
- `test_grade_backlog_skips_graded_and_spends_zero_cognition_when_none_pending`
- `test_grade_backlog_resumes_by_rederiving_pending_from_labels`
- `test_grade_backlog_one_issue_failure_never_stops_the_batch`

and every pre-existing zero-token guard test stays green untouched.

## Live validation scenarios (post-merge, real pipeline)

1. **Adopt one hand-written issue** — pick a finance-sentry issue with a clear
   surface + change + intent; call `regrade_intake` with its URL. Expect:
   `devclaw-ready` label + mirror comment on the issue; body untouched.
2. **Adopt a vague issue** — pick a wish-style issue. Expect:
   `needs-refinement` + a comment naming at least one concrete missing element.
3. **Bulk onboard** — call `grade_backlog("finance-sentry")`. Expect: every
   open issue in exactly one report bucket; ≤20 graded; issues from (1)/(2)
   under `skipped_already_graded`; zero cognition if everything is graded.
4. **Resume** — if a remainder was reported, call `grade_backlog` again.
   Expect: exactly the remainder is graded; nothing re-graded.
5. **Downstream indistinguishability** — dispatch a goal referencing an adopted
   `devclaw-ready` issue (companion mode). Expect: identical behavior to an
   intake-filed issue (SC-006).
```
