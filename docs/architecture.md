# devclaw architecture (spec 046)

Some Python for what is determined, a model for what is reasoned, and one line
between them. This page is the mental model plus the locked contracts. The
design with its use cases is [`../specs/046-devclaw-v2/spec.md`](../specs/046-devclaw-v2/spec.md).

## Part I — the mental model

### Four layers, one direction

```
user ─► OpenClaw waiter ─► MCP tools            devclaw/server/
                              │
                              ▼
                        the goal layer          devclaw/goal/      the tick rule, the world, the two prompts, the done-gate
                              │  submit / pump
                              ▼
                        queue + engine          devclaw/task_queue.py, queue/, engine/, gates.py, delivery/
                              │  docker run --rm
                              ▼
                        the worker harness      runner/runner.py   inside the sandbox: ACP agent, skills, the verify loop
```

The import order is declared in `pyproject.toml` (`[tool.importlinter]`) and
`lint-imports` fails a new upward edge. Only the goal layer reads GitHub as
state.

### The tick rule

Every `DEVCLAW_GOAL_TICK_SECONDS` (default 15 min), and at once when a session
settles or a webhook arrives, for every open goal (`devclaw/goal/tick.py`):

1. A session running for the goal → nothing. Dispatch held (quota pause,
   operator hold, run window) → nothing. Sessions today ≥ the cap → nothing.
2. Read the world (`devclaw/goal/world.py`): the PR for `goal/<id>` (head,
   state, mergeability, the check rollup and failing logs), each referenced
   issue, every comment on the issue and the PR, the credentials that probe
   green.
3. PR merged → close the goal `achieved`.
4. The last session ended `BLOCKED` and its question is not on the thread yet →
   post it (a `<!-- devclaw:block -->` record) and ping once.
5. Blocked — a devclaw stop newer than the owner's last instruction → nothing.
6. Red CI on a delivered head, not yet recorded → post the failing logs as a
   block and ping. Never retried (ruled 2026-09-13: the sandbox already ran
   what CI runs; a red CI is an environment gap).
7. Last exit `INTERRUPTED` → spawn (resume the session's own unfinished work).
8. Last exit `REVIEW` → post the verdict once; achieved → squash-merge and
   close; refused → blocked.
9. Last exit `DONE` → CI pending: wait; CI green and no verdict for this head:
   spawn the read-only review session.
10. Fingerprint unchanged since the last session was given it → nothing.
11. Otherwise → spawn ONE session with the world as facts, and record the
    fingerprint in the same transaction.

One session per project at a time: a project whose goal has a live task is
skipped; a blocked goal does not hold the lane.

### The world fingerprint

`{pr state, head sha, ci state, newest instruction id, newest devclaw record
id, green credentials, issue states}`. Stored on the goal row as `last_seen`
— an observation whose loss costs one session. The session's exit line is NOT
in it: a session that pushed nothing changed nothing.

### The owner's one channel

A comment on the issue or PR that mentions `@devclaw` (`DEVCLAW_MENTION`).
The `decide` tool posts one and records a Decision. Everything devclaw writes
on a thread carries `<!-- devclaw:<kind> k=v -->` and is a fact, never an
instruction: `block` (task, head, why) and `verdict` (head, task, achieved,
unreadable).

### One session

`devclaw/queue/settle.py`. Place the goal branch in the goal's own checkout
(`<project>/.goals/<id>`), capture the pre-run sha, run the engine once under
the wall clock, then classify how it ended:

| Result | Exit | Row |
|---|---|---|
| quota / auth / outage | (requeued) | account paused, work snapshotted, `pause_count` +1 |
| timeout, harness error, red local verify, delivery failure | `INTERRUPTED` | failed — resumed next tick |
| a gate refused the span (gate-input edit, binary, undeterminable span, gutted tests) | `REFUSED` | failed — the next session is told why; twice in a row blocks |
| the session said `BLOCKED` | `BLOCKED` | done — the question goes on the thread |
| ok, span delivered | `DELIVERED` / `DONE` / `NOTHING` (the session's own line) | done, with the PR url |
| a review session | `REVIEW` | done — the verdict JSON in the result |

### The done-gate

A `DONE` proposal with green CI spawns a `review_repository` session over the
delivered head with `devclaw/prompts/done-gate.md`: decompose the contract
into clauses, cite evidence per clause, answer in JSON. `devclaw/goal/donegate.py`
validates it mechanically — achieved only when every clause is satisfied with
evidence and no question is open; no readable JSON is `unreadable` and never
passes. The verdict is posted on the PR; achieved squash-merges
(`gh pr merge --squash`) and closes the goal.

## Part II — the locked contracts

### What software owns (pillar 2)

| Domain | Where |
|---|---|
| Safety | the sandbox (`engine/sandcastle.py`: `--cap-drop ALL`, `--pids-limit`, memory cap, tmpfs over `.claude`), the credential strip at every spawn site, `credentials.py` as the one registry |
| Money | the pause classifier (`loom/limits.py`) + `set_global_pause`; `DEVCLAW_GOAL_SESSIONS_PER_DAY`; `DEVCLAW_MAX_CONCURRENT`; the host-memory admission and workspace breaker (`queue/admission.py`) |
| State | `state_store/`: only the TaskQueue mutates task rows; the goal layer writes goals, decisions and `last_seen`; one `transaction()` |
| Verdict | the project's CI on the delivered head (`goal/github.py`), the materialized span (`task_change.py`), the four gates (`gates.py`), the done-gate validator |
| Protocol | the runner payload (`engine/__init__.py`), the four exit lines, the review JSON, the MCP tools |

### What the host stores (pillar 5)

`goals` (identity, `last_seen`, outcome), `decisions`, `tasks` + `events`
(the execution record), `meta` (the pause, the hold, the window, the dial,
the deploy intent). `tests/test_goal_layer_stays_readable.py` fails the build
on a judgment column and on a goal layer past 1,500 lines.

### The sandbox runs what CI runs

The runner (`runner/runner.py`) runs `.devclaw/verify` after the agent's turn
and hands a red run back to the same session, up to `DEVCLAW_VERIFY_ROUNDS`;
a code-writing session that leaves no script after being asked ends with a
failed verify. The host's `verify` gate then fails a red run closed
(`INTERRUPTED`, resumed).

A green run is not yet a pass. The same gate then checks that the first-run
manifest — `.devclaw/verify` and `.devclaw/workflow.md`, how the repo verifies
and how it plans — is TRACKED in the git index, not merely present in the
working tree. A repository whose `.gitignore` swallows `.devclaw/` would
otherwise deliver a branch carrying neither, and every later session would
re-derive from scratch with nothing able to notice. The untracked path is named
back to the session, which un-ignores and commits it.

### Failure is loud

A gate crash fails closed. A delivery that cannot push fails the row. A
missing skill bundle refuses to run. An unreadable verdict never passes. A
world the host cannot read (`gh` down) does nothing and says so.

## The code map

```
devclaw/server/      tools/ (goals, projects, control, doctor) · routes/ (health, control, goals, projects, tasks, webhooks, console) · lifecycle.py
devclaw/goal/        tick.py · world.py · prompts.py · donegate.py · github.py · service.py · notify.py · self_deploy.py
devclaw/prompts/     session.md · done-gate.md
devclaw/task_queue.py · queue/settle.py · queue/admission.py
devclaw/gates.py · task_change.py · delivery/__init__.py
devclaw/engine/      sandcastle.py · host.py · stub.py · workspace.py · runner_io.py
devclaw/state_store/ core.py · schema.py · rows.py · control.py · events.py · goals.py
devclaw/loom/        limits.py · test_integrity.py · untrusted.py
devclaw/credentials.py · probes.py · config.py · project_registry.py · doctor/ · cli.py
runner/              runner.py · acp_client.py · skills/ · hooks/
```
