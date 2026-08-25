# Tasks: PR Authorship from Agent Commit

## [US1] PR title/body from agent commit, never the dispatch prompt

- [x] Fix `_resolve_title` no-commit fallback: return `MACHINE_COMMIT_SUBJECT` instead of goal-derived text
- [x] Fix `_pr_body` else-branch: replace `goal.strip()` with `_NO_AGENT_COMMIT_LEAD` constant
- [x] Fix `materialization_message` / dirty-tree commit in `deliver_change`: use `MACHINE_COMMIT_SUBJECT`; drop `title` param
- [x] `sandcastle._build_docker_args`: re-expose `.claude/rules/` via nested bind-mount when present (hooks/settings.json stay blocked)
- [x] Fix test collection: use `collect_ignore` in `tests/conftest.py` when hook file absent (not `pytest.skip()` in the test file)
- [x] Regression tests: `test_resolve_title_no_worker_commit_returns_machine_commit_subject`, `test_pr_body_never_echoes_dispatch_prompt_when_no_agent_commit`, sandbox isolation tests
- [x] Integration test: `test_run_sandcastle_passes_through_workspace_claude_rules` — exercises `run_sandcastle` with a workspace containing `.claude/rules/` and asserts the read-only rules mount appears in the docker argv (steering clauses 6+7)
- [x] Commit + spec artifacts together

## [US2] Goal-branch PR body never echoes the dispatch prompt

- [x] Fix `_goal_pr_body`: add `changes: str | None = None` kwarg; replace `goal.strip()` lead with `changes`/`_NO_AGENT_COMMIT_LEAD` pattern matching `_pr_body`
- [x] Update `_goal_pr_body` call site in `deliver_change` to pass `changes=changes`
- [x] Update `_closes_issues` call inside `_goal_pr_body` to include `changes` (mirrors `_pr_body`)
- [x] Update `test_goal_branch_pr_body_never_renders_the_advance_brief` to match new behavior
- [x] Add `test_goal_pr_body_never_echoes_dispatch_prompt_when_no_agent_commit` (steering clause 2)
- [x] Add `test_goal_pr_body_instruction_text_never_leaks` (steering clause 5)
- [x] Commit + spec artifacts together

## [US3] Goal-branch multi-increment PR title from latest commit, not dispatch prompt

- [x] Fix `deliver_change` multi-increment else-branch: use `subjects[-1]` (latest commit) or `MACHINE_COMMIT_SUBJECT` instead of `goal`/advance-brief heuristic
- [x] Update `test_deliver_goal_branch_refreshes_the_existing_pr_to_accumulated_state`: assert title from latest commit subject ("M2"), not goal text ("Ledger")
- [x] Add `test_goal_branch_multi_increment_title_never_echoes_dispatch_prompt`: assert IMPORTANT:/branch-hint/retry text absent from multi-increment goal-branch title
- [x] Commit + spec artifacts together
