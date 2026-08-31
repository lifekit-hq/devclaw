# Tasks — spec 027 code-map-brief

**Status**: IMPLEMENTED — all tasks done, PR pending.

## US1 — Architecture map pointer at dispatch (P1)

- [x] T001 Add `architecture_map_pointer(workspace_dir: str) -> str` to `devclaw/goal/repo_brief.py`: probe for `ARCHITECTURE.md` at workspace root, return a pointer section on hit, `""` on miss or any OS error; best-effort never-raises
- [x] T002 In `devclaw/goal/tick_dispatch.py` `_dispatch_action`: call `architecture_map_pointer(goal.workspace_dir)` and prepend the result to `brief_prefix` (before the repo-notes brief, same `action.tool != "review_repository"` guard)
- [x] T003 In `runner/skills/_common.md`: add a standing instruction — if `ARCHITECTURE.md` exists at the repo root, read it before exploring (after the AGENTS.md step, before the per-repo .agent/skills/ step)
- [x] T004 Write `tests/test_repo_brief.py` with two named regression tests:
  - `test_dispatch_includes_architecture_pointer_when_map_exists` — tmp_path with ARCHITECTURE.md → pointer in prefix
  - `test_dispatch_skips_architecture_pointer_when_no_map` — tmp_path without → no pointer
- [x] T005 In `tests/test_runner_skills.py`: add named test `test_common_skill_instructs_reading_architecture_map` asserting the ARCHITECTURE.md pointer instruction is present in the always-on brief for every kind; no ceiling bumps needed (brief grew by ~150 chars, still under 13 200 ceiling)

## US2 — Brief retention: raise the cap (P2)

- [x] T006 In `devclaw/goal/repo_brief.py`: raise `MAX_BRIEF_CHARS` from 4 000 to 12 000
- [x] T007 In `tests/test_repo_brief.py`: add named test `test_brief_retains_facts_under_raised_cap` — merge 5 000 chars of existing brief + new notes, assert all original lines retained (no eviction)

## Polish

- [x] T008 Run full suite + `ruff check .` + `mypy` — all green; 1168 passing, 1 pre-existing failure (`.claude/hooks` tmpfs shadow, unrelated)
