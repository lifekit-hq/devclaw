# One session, hop by hop

```
tick (goal layer, host)            queue (host)                    session (claude -p in the worktree)
────────────────────────────       ───────────────────────────     ───────────────────────────────────
read the world (gh)                claim the row                   skills + the session brief → claude -p
compare to last_seen               prepare_workspace(goal/<id>)    (or --resume the quota-paused session)
lease the goal's treehouse         capture pre_run_sha             the agent works, commits
  worktree (held until close)      run the engine (stream watchdog) .devclaw/verify runs; red → --resume
submit the row + record last_seen                                  result: ok | blocked | error
                                   classify the exit
                                   materialize the span (task_change)
                                   gates: verify · materialize · change_class · integrity
                                   deliver: push goal/<id>, open or refresh the one PR
                                   mark_done(pr_url, exit) — one write
tick again (settle wakes it)
```

**Fails if** — every hop names its failure:

- the world cannot be read → `unreadable`, nothing spawns, logged;
- no treehouse worktree can be leased → `unleased`: an `INTERRUPTED` task is recorded carrying the lease error (`workspace prep failed: no worktree leased: …`) and no session runs; the next tick retries — each retry is another row, so it stops at the goal's daily session cap (`DEVCLAW_GOAL_SESSIONS_PER_DAY`) — and the retry resumes as `implement_feature` like any interrupted task;
- the goal checkout cannot be placed on the branch → `INTERRUPTED`, resumed;
- the engine refuses (missing skills) → `INTERRUPTED`;
- a usage limit → the account pauses, the work is snapshotted, the row requeues
  with its tree kept and `--resume`s its own session — the only path that resumes one;
- no first frame within `DEVCLAW_FIRST_FRAME_S`, or a silent stream for
  `DEVCLAW_SILENCE_S` → the session's process group is killed, the tree is
  snapshotted, `INTERRUPTED`; the next row resets the goal branch and starts a
  fresh session (never `--resume` — the old session's edits are not in the tree);
- the verify is red after `DEVCLAW_VERIFY_ROUNDS` → `INTERRUPTED` (the session's own unfinished work);
- a gate refuses the span → `REFUSED`, the next session is told why; twice in a row → blocked;
- the push or PR fails → `INTERRUPTED` with the reason.

The request (`engine/__init__.py` → `EngineRequest`): `kind`, `workspace_dir`
(the goal's worktree), `goal` (the brief), `verify_cmd` (None: the engine
discovers `.devclaw/verify`), `resume_session_id` (the row's own session, set only
when a quota pause requeued it), `on_session` (persists the session id the moment the stream names
it). The session's env is the host's less the refused metered keys and the
host-only credentials — `GH_TOKEN`, the MCP bearer `DEVCLAW_TOKEN`, the
`DEVCLAW_GOAL_NOTIFY_URL` webhook (`credentials.session_env`). The result: `status`,
`agent_output` (the final message, exit line included), `verify`, `usage`,
`session_id`.
