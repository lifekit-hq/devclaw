# Handoff — 2026-08-20, end of session

## Where things stand

Branch `refactor/speckit-native-amputation`, pushed. Tag
`pre-amputation-v0.3.0` → `8fe277f`, pushed. Working tree clean.

| Commit | What | Suite |
|---|---|---|
| `fcfdf50` | spec + clarify + baseline | — |
| `be5fb1a` | plan, research, data-model, contracts, quickstart | — |
| (3a) | spike code deleted | 1952 passed |
| (3b) | trend detector deleted | **1873 passed, 4 skipped** |

Baseline was 1990. The drop is expected and correct — removed tests belong to
removed mechanisms (see `baseline.md`). **Zero failures is the gate, not count
parity.**

## Worktree

```
/private/tmp/claude-501/-Users-dsdevq-Projects-devclaw/362d3bb6-7942-4e55-9b09-90ecbded553e/scratchpad/012-amputation
```

Scratchpad path — may not survive a reboot. Re-create with:
`git worktree add <path> refactor/speckit-native-amputation`

**The worktree has no `.venv`.** Run tests with the main checkout's interpreter
FROM the worktree root, so cwd precedes site-packages:

```bash
cd <worktree>
/Users/dsdevq/Projects/devclaw/.venv/bin/python -c "import devclaw; print(devclaw.__file__)"   # must print the worktree
TMPDIR=$(mktemp -d) /Users/dsdevq/Projects/devclaw/.venv/bin/python -m pytest -q
```

## Next up: T018–T034 (Phase 3c/3d — the program vocabulary)

The big one, and the only genuinely risky part of the arc.

**Do whole functions first (3c), then call sites (3d).** The map is in
`plan.md`; per-function reference counts are in `research.md`.

**THE TRAP — read `data-model.md` before touching `state_store/core.py`:**
`list_pending_standalone` selects `WHERE program_id IS NULL`. FR-001a keeps the
column populated in history, so **that guard must survive the cut**. Deleting it
as "now vacuous" lets an orphaned pending program-child row be claimed and
launched. T033 adds the regression test that pins it.

## Lesson from this session — cost me a rerun

Do NOT delete a test with a regex that scans forward to "the next def". In
`test_goal_tick.py` that swallowed a `PruningEngine` helper defined after the
target and broke two unrelated zero-token tests. Cut by **exact line bounds**,
and assert the removed blob contains the target and nothing else.

## Not done

- T035–T041 superseded singles (repo_brief, self_issue, merge, elicitation,
  slice_guard's PLAN.md half)
- T042–T046 US2 dispatch-tool sugar
- T047–T053 US3 brief trim + the PR #69 leak fix
- T054–T061 docs, rules, final validation

## Open items outside the code

- **PR #69** (lifekit-dashboard) is still open with the leaked title/body, and
  its `Closes #64` is wrong — #64 is a PR, and issues #65/#62 were missed
  because `_ISSUE_RE` only matches the singular "issue #N". Merging it will not
  close them.
- The VPS runs `468b072` (v0.2.0), two commits behind `main`. **Nothing from
  this branch is deployed.**
- Recommendation on record: do NOT deploy this arc into a night window before a
  live shakedown. The stubbed suite cannot catch a broken sandbox launch.
