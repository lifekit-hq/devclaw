# One session, hop by hop

```
tick (goal layer, host)            queue (host)                    session (claude -p in the worktree)
────────────────────────────       ───────────────────────────     ───────────────────────────────────
read the world (gh)                claim the row                   skills + the session brief → claude -p
compare to last_seen               prepare_workspace(goal/<id>)    (or --resume the interrupted session)
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
- no treehouse worktree can be leased → `unleased`, nothing spawns, the next tick retries;
- the goal checkout cannot be placed on the branch → `INTERRUPTED`, resumed;
- the engine refuses (missing skills) → `INTERRUPTED`;
- a usage limit → the account pauses, the work is snapshotted, the row requeues;
- no first frame within `DEVCLAW_FIRST_FRAME_S`, or a silent stream for
  `DEVCLAW_SILENCE_S` → the session's process group is killed, the tree is
  snapshotted, `INTERRUPTED`, and the next session resumes it (`--resume`);
- the verify is red after `DEVCLAW_VERIFY_ROUNDS` → `INTERRUPTED` (the session's own unfinished work);
- a gate refuses the span → `REFUSED`, the next session is told why; twice in a row → blocked;
- the push or PR fails → `INTERRUPTED` with the reason.

The request (`engine/__init__.py` → `EngineRequest`): `kind`, `workspace_dir`
(the goal's worktree), `goal` (the brief), `verify_cmd` (None: the engine
discovers `.devclaw/verify`), `resume_session_id` (the interrupted session to
continue), `on_session` (persists the session id the moment the stream names
it). The session's env is the host's less the refused metered keys and the
host-only credentials (`credentials.session_env`). The result: `status`,
`agent_output` (the final message, exit line included), `verify`, `usage`,
`session_id`.
