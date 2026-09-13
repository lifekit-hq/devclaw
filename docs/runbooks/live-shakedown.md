# Live shakedown — the real pipeline on one goal

The pytest suite is fully stubbed. This is the only way to see the real
pipeline: a logged-in `claude`, docker, a `GH_TOKEN`, a real repository.

1. `docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .`
2. `DEVCLAW_TRANSPORT=http devclaw-mcp` with `DEVCLAW_ENGINE` unset.
3. `register_project(project_id, name, repo_url, workspace_dir)`.
4. File an issue on the repo with a `## Done when` section; `create_goal(goal_id, project_id, issues=[N])`.
5. Watch `get_goal` and the goal's thread: the first session opens the PR
   (`DELIVERED`), CI runs, later sessions continue until one says `DONE`; the
   review session's verdict lands on the PR; the merge closes the goal.
6. Provoke the stops on purpose: reply `@devclaw …` on a `BLOCKED` question;
   make CI red and see the goal stop with the log; `decide` and watch it wake.

`doctor` first if anything looks wrong; `get_events(task_id)` for a session's
turn-by-turn record.
