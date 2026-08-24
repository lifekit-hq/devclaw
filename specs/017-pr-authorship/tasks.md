# Tasks: PR Authorship from Agent Commit

## [US1] PR title/body from agent commit, never the dispatch prompt

- [x] Fix `_resolve_title` no-commit fallback: return `MACHINE_COMMIT_SUBJECT` instead of goal-derived text
- [x] Fix `_pr_body` else-branch: replace `goal.strip()` with `_NO_AGENT_COMMIT_LEAD` constant
- [x] Fix `materialization_message` / dirty-tree commit in `deliver_change`: use `MACHINE_COMMIT_SUBJECT`; drop `title` param
- [x] `sandcastle._build_docker_args`: re-expose `.claude/rules/` via nested bind-mount when present (hooks/settings.json stay blocked)
- [x] Fix test collection: use `collect_ignore` in `tests/conftest.py` when hook file absent (not `pytest.skip()` in the test file)
- [x] Regression tests: `test_resolve_title_no_worker_commit_returns_machine_commit_subject`, `test_pr_body_never_echoes_dispatch_prompt_when_no_agent_commit`, sandbox isolation tests
- [x] Commit + spec artifacts together
