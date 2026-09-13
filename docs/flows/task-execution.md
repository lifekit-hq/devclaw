# One session, hop by hop

```
tick (goal layer, host)            queue (host)                    sandbox (docker run --rm)
────────────────────────────       ───────────────────────────     ───────────────────────────────
read the world (gh)                claim the row                   runner.py reads the payload
compare to last_seen               prepare_workspace(goal/<id>)    skills + the session brief → ACP agent
submit the row + record last_seen  capture pre_run_sha             the agent works, commits
                                   run the engine (wall clock)     .devclaw/verify runs; red → back to the agent
                                                                    result line: ok | blocked | error | rate_limited
                                   classify the exit
                                   materialize the span (task_change)
                                   gates: verify · materialize · change_class · integrity
                                   deliver: push goal/<id>, open or refresh the one PR
                                   mark_done(pr_url, exit) — one write
tick again (settle wakes it)
```

**Fails if** — every hop names its failure:

- the world cannot be read → `unreadable`, nothing spawns, logged;
- the goal checkout cannot be placed on the branch → `INTERRUPTED`, resumed;
- the runner refuses (missing skills, a declared credential absent) → `INTERRUPTED`;
- a usage limit → the account pauses, the work is snapshotted, the row requeues;
- the wall clock → the sandbox is torn down, the tree is snapshotted, `INTERRUPTED`;
- the verify is red after the runner's rounds → `INTERRUPTED` (the session's own unfinished work);
- a gate refuses the span → `REFUSED`, the next session is told why; twice in a row → blocked;
- the push or PR fails → `INTERRUPTED` with the reason.

The payload (`engine/__init__.py` → `sandcastle._build_payload`): `kind`,
`workspace_dir` (`/workspace`), `goal` (the brief), `model`, `acp_command`,
`verify_cmd` (None: the runner discovers `.devclaw/verify`), `agent_env`
(credential NAMES the registry declares). The result: `status`,
`agent_output` (the final message, exit line included), `verify`, `usage`.
