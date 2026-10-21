# One session, hop by hop

```
tick (goal layer, host)            queue (host)                    session (claude -p in the worktree)
────────────────────────────       ───────────────────────────     ───────────────────────────────────
read the world (gh)                claim the row                   skills + the session brief → claude -p
compare to last_seen               prepare_workspace(goal/<id>)    (or --resume the quota-paused session)
lease the goal's treehouse         capture pre_run_sha             the agent works, commits
  worktree (held until close)      run the engine (stream watchdog) .devclaw/verify runs; red → --resume
submit the row + record last_seen                                  result: ok | blocked | error
                                   (the engine) no-mistakes init   green + unpushed → the same session
                                                                     runs no-mistakes axi run --skip ci:
                                                                     review · test · document · lint ·
                                                                     push goal/<id> · open/update the PR
                                   classify the exit (red verify,
                                     no passed run → INTERRUPTED)
                                   mark_done(pr_url, exit) — one write
tick again (settle wakes it)
```

**Fails if** — every hop names its failure:

- the world cannot be read → `unreadable`, nothing spawns, logged;
- no treehouse worktree can be leased, or treehouse cannot say whether the goal already holds one → `unleased`: an `INTERRUPTED` task is recorded carrying the lease error (`workspace prep failed: no worktree leased: …`) and no session runs; the next tick retries — each retry is another row, so it stops at the goal's daily session cap (`DEVCLAW_GOAL_SESSIONS_PER_DAY`) — and the retry runs as `implement_feature` in a fresh session, like any interrupted task's successor;
- the goal checkout cannot be placed on the branch → `INTERRUPTED`; the next tick starts a fresh session;
- the engine refuses (missing skills) → `INTERRUPTED`;
- a usage limit → the account pauses, the work is snapshotted, the row requeues
  with its tree kept and `--resume`s its own session — the only path that resumes one;
- no first frame within `DEVCLAW_FIRST_FRAME_S`, or a silent stream for
  `DEVCLAW_SILENCE_S` → the session's process group is killed, the tree is
  snapshotted, `INTERRUPTED`; the next row resets the goal branch and starts a
  fresh session (never `--resume` — the old session's edits are not in the tree);
- the verify is red after `DEVCLAW_VERIFY_ROUNDS` (each round `--resume`s the same session in-run) →
  `INTERRUPTED`; the next row starts a fresh session on the reset goal branch;
- the hand-off did not ship (`no-mistakes init` failed, or the branch's newest no-mistakes run does not carry the session's head or did not pass) → `INTERRUPTED` with the reason; a gate question only the owner can settle → `BLOCKED`.

The request (`engine/__init__.py` → `EngineRequest`): `kind`, `workspace_dir`
(the goal's worktree), `goal` (the brief), `verify_cmd` (None: the engine
discovers `.devclaw/verify`), `resume_session_id` (the row's own session, set only
when a quota pause requeued it), `on_session` (persists the session id the moment the stream names
it), `hand_off` (the row's `deliver` flag: ship a green, unpushed branch through
no-mistakes), `closes` (the goal's issues, passed as `--closes`). The session's env is the host's less the refused metered keys and the
host-only credentials — `GH_TOKEN`, the MCP bearer `DEVCLAW_TOKEN`, the
`DEVCLAW_GOAL_NOTIFY_URL` webhook (`credentials.session_env`); `no-mistakes init`
runs under the host env less only the metered keys, so the daemon it starts can
push and open the PR. The result: `status`, `agent_output` (the final message,
exit line included), `verify`, `pr_url` (when the hand-off shipped), `usage`,
`session_id`.

Both the `claude -p` child and the `.devclaw/verify` child run in their own
process group, and the group is killed when the child exits — normally or not —
so a dev server or watcher a session backgrounded does not outlive it. Each
also raises its `oom_score_adj` to 800 (never lowers it; silent if the kernel
refuses), so under memory pressure it, not the server sharing the container's
cgroup, is the OOM killer's preferred victim.
