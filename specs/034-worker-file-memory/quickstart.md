# Quickstart — validating spec 034

## Stubbed (pytest, no docker/claude)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_repo_brief.py tests/test_doctor.py tests/test_runner_skills.py \
  tests/test_runner_acp.py tests/test_acp_client.py tests/test_goal_tick.py
```

Expected:
- `test_repo_brief.py`: the memory pointer appears iff `.devclaw/MEMORY.md`
  exists in the checkout; `None` / missing workspace ⇒ `""` (SC-004 guard).
- `test_doctor.py`: seeded `.devclaw/` ⇒ OK; a dangling index line, an
  unindexed fact, or > 30 entries ⇒ WARN naming the item; a lingering
  `project_docs` table ⇒ the legacy dropped-shapes check FAILs.
- `test_runner_skills.py`: the return contract carries no `REPO NOTES`
  line; the writes-code bundle carries the write policy and stays under the
  cap; `_common` carries the read protocol.
- Runner ACP tests: the fake agent's hand-back no longer emits REPO NOTES
  and the result payload has no `repo_notes` key.

## Live (after deploy; the sandbox image must carry the new skills)

1. `doctor` — every project reports `project.manifest.revision` WARN
   (behind 3) until re-onboarded; `project.worker_memory.health` OK.
2. Re-onboard one project → the migrate PR adds `.devclaw/MEMORY.md`
   (seeded, empty facts list). Merge it.
3. Dispatch a task on that project. In the goal log the `dispatch brief:
   N chars` line is ≥ 40 % smaller than the last pre-deploy brief on the
   note-heaviest project (SC-001, 2026-09-01 baseline 9.4 KB on devclaw);
   the worker's event stream shows it reading `.devclaw/MEMORY.md`.
4. When the worker learns a repo fact, the increment's PR diff shows a
   `.devclaw/memory/<slug>.md` file and an index line (SC-002/SC-003 are
   read over the following nights: no two files restating one rule).
5. `SELECT name FROM sqlite_master WHERE name='project_docs'` on the box
   returns nothing after the first boot on this build.
