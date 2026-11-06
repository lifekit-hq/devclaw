# devclaw architecture (spec 046)

Some Python for what is determined, a model for what is reasoned, and one line
between them. This page is the mental model plus the locked contracts. The
design with its use cases is [`../specs/046-devclaw-v2/spec.md`](../specs/046-devclaw-v2/spec.md).

## Part I — the mental model

### Four layers, one direction: what the re-cut kept and replaced

```
MCP client ─► MCP tools                 devclaw/server/       KEPT   tools, routes, the console, auth
                 │
                 ▼
           the goal layer               devclaw/goal/         KEPT   the tick rule, the world, the two prompts, the done-gate
                 │  submit / pump
                 ▼
           queue + engine               devclaw/task_queue.py, queue/, engine/
                 │                                            KEPT   one session: place the branch, run, settle the exit; the quota pause
                 │  claude -p (stream-json, --resume)
                 ▼
           the session engine           engine/claude.py      NEW    replaces runner/ (an ACP worker on a 60-minute wall clock):
                 │                                                   the stream, its silence watchdog, skills, the verify loop
                 │  in the goal's worktree
                 ▼
           a treehouse worktree         engine/workspace.py   NEW    replaces the per-task docker sandbox and the .goals/<id> clones:
                 │                                                   one lease per goal, returned on close; not a sandbox
                 │  no-mistakes axi run
                 ▼
           no-mistakes                  (an external tool)    NEW    replaces the home-built gates and delivery/:
                                                                     review, test, lint, push, PR
```

KEPT is devclaw's own part: the supervision, the done-gate and the owner's
channel. NEW is a thin seam over an off-the-shelf single-purpose tool —
`claude -p`, treehouse, no-mistakes — where home-built machinery ran before
the re-cut (#952, #953, #954). Any MCP client (Claude Code, a chat agent)
drives the tools; the console reads the same state over HTTP.

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
   block and ping. Never retried (ruled 2026-09-13: the session already ran
   what CI runs; a red CI is an environment gap).
7. Last exit `INTERRUPTED` → spawn a fresh session of the same kind (a code
   session continues the unfinished work from the goal branch; an interrupted
   review is a review again).
8. Last exit `REVIEW` on the head the review read → post the verdict once;
   achieved → squash-merge and close (a hard-to-reverse change first posts a
   `hard_to_reverse` block and merges only after the owner answers `merge`);
   refused → blocked until the owner answers. A PR that moved since the review read it falls through.
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
instruction: `block` (task, head, why — `hard_to_reverse` is the merge wait) and `verdict` (head, task, achieved,
unreadable).

### One session

`devclaw/queue/settle.py`. Place the goal branch in the goal's own treehouse
worktree (leased per goal, returned when the goal closes), capture the pre-run
sha, run the engine once, then classify how it ended:

| Result | Exit | Row |
|---|---|---|
| quota / auth / outage | (requeued) | account paused, work snapshotted (a read-only review is not), `pause_count` +1; the tree is kept and the row `--resume`s its own session |
| stream silence (`DEVCLAW_FIRST_FRAME_S` / `DEVCLAW_SILENCE_S`), harness error, red local verify, a hand-off that did not ship | `INTERRUPTED` | failed — next tick a fresh session runs on the reset goal branch (never `--resume`) |
| the session said `BLOCKED` | `BLOCKED` | done — the question goes on the thread |
| ok, branch shipped through no-mistakes (or nothing to ship) | `DELIVERED` / `DONE` / `NOTHING` (the session's own line) | done, with the PR url |
| a review session | `REVIEW` | done — the verdict JSON in the result; failed when it moved HEAD or changed a tracked file (read-only is checked, never trusted) |

### The done-gate

A `DONE` proposal with green CI is first scope-checked (`_scope_gate` in
`goal/tick.py`): the PR's changed files (`gh pr diff --name-only`) must each be
named — by path, directory, file name, or a test or spec sharing a named file's
stem — in the goal's objective, the issues or the owner's comments (a token
counts as a file only with a `/` or if the base tree has it, so `Node.js` is
prose; `.devclaw/`, `docs/` and a lockfile whose manifest is named or changed
are always in scope; text naming no file states no scope). A file named nowhere
posts a `scope` record and spawns one correction session; a second excess
stops the goal with `SCOPE_EXCEEDED`; the owner's newer comment waives that head only; an
unreadable file list never passes. Then it spawns a `review_repository` session over the
delivered head with `devclaw/prompts/done-gate.md`: decompose the contract
into clauses, cite evidence per clause, answer in JSON. `devclaw/goal/donegate.py`
validates it mechanically — achieved only when every clause is satisfied with
evidence and no question is open; no readable JSON is `unreadable` and never
passes. A `CONSTRAINT:` line the PR adds to `.devclaw/workflow.md` (a session's
proven constraint, #980) with no clause or test behind it is a concern the review
names; the line reaches `main` only through the squash-merge below, so the next
goal's sessions read it only after an achieved goal. The verdict is posted on the PR; achieved squash-merges
(`gh pr merge --squash --match-head-commit <the judged head>`) and closes
the goal.

The merge has one more rule (#979, `devclaw/goal/blast.py`): at the merge edge
the goal is classified live, with no model call, as `hard` / `wide` /
`reversible` from the issues' `## Done when` text (migrate, delete, drop table,
purge, production data …) and the PR's changed paths (`migrations/`,
`alembic/`, `*.sql`). Only `hard` changes the outcome: it posts a `block`
record (`why=hard_to_reverse`) and waits for a comment mentioning the bot
whose whole text is `merge` (`decide(goal, "merge")`), newer than that record
and written by the account the host's own `gh` credential acts as (the one
`decide` posts through; `gh api user`, read when needed, no config) or whose
`author_association` is `OWNER`; any other or missing author does not release
it, and an unreadable host identity leaves only `OWNER`. While it waits, each
newer instruction that is not that answer is not applied: the block notice is
posted again once for it, with no session spawned. The class is never stored
(pillar 5); a moved head asks again.

## Part II — the locked contracts

### What software owns (pillar 2)

| Domain | Where |
|---|---|
| Safety | one treehouse worktree per goal (`engine/workspace.py`, leased at the first spawn, returned on close) — **not a filesystem sandbox**; the credential strip at every spawn site (`credentials.session_env`), `credentials.py` as the one registry |
| Money | the pause classifier (`loom/limits.py`) + `set_global_pause`; `DEVCLAW_GOAL_SESSIONS_PER_DAY`; `DEVCLAW_MAX_CONCURRENT`; the workspace breaker (`queue/admission.py`) |
| State | `state_store/`: only the TaskQueue mutates task rows; the goal layer writes goals, decisions and `last_seen`; one `transaction()` |
| Verdict | the project's CI on the delivered head (`goal/github.py`), the session's verify run, the passed no-mistakes run the engine requires before a hand-off counts (`engine/claude.py`), the done-gate validator |
| Protocol | the engine request (`engine/__init__.py`), the four exit lines, the review JSON, the MCP tools |

### What the host stores (pillar 5)

`goals` (identity, `last_seen`, outcome), `decisions`, `tasks` + `events`
(the execution record), `meta` (the pause, the hold, the window, the dial,
the deploy intent), and `user_preferences`: the console's appearance per gate
identity (the `X-Auth-Request-Email` the sso forwardAuth sets), written only by
`PATCH /api/me/preferences`. It is a record of what a user chose, never a
judgment. `tests/test_goal_layer_stays_readable.py` fails the build
on a judgment column and on a goal layer past 1,500 lines.

### The session runs what CI runs

The engine (`engine/claude.py`) runs `.devclaw/verify` after the agent's turn
and hands a red run back to the same session (`--resume`), up to `DEVCLAW_VERIFY_ROUNDS`;
a code-writing session that leaves no script after being asked ends with a
failed verify. The host then fails a red run closed (`queue/settle.py`:
`INTERRUPTED`; the next session starts fresh on the reset goal branch).

Two host facts reach the session instead of being found at the end of it (#987).
Before a code session's first turn, a repository that drives a browser
(`playwright.config.*`) on a host whose Chromium cannot load a shared library
(`ldd`) gets one `Host preflight:` paragraph at the top of its prompt and a
`Preflight` event; an inconclusive check is silent. The worker image installs
Playwright's Chromium system libraries as root and carries the .NET SDK that
finance-sentry's `.devclaw/verify` and lifekit-dashboard's gates run (#987), so
that preflight fires only on a stale build. Chromium cannot build its own
sandbox in the container (Docker's default seccomp profile withholds user
namespaces; widening it was refused, ruled 2026-11-06): Playwright's Chromium runs
because Playwright starts it with `--no-sandbox`, and a browser a test launches
itself from one executable path (devclaw's impeccable design scan in
`frontend/e2e/quality.spec.ts`) goes through `/usr/local/bin/chromium-no-sandbox`,
a wrapper that adds the flag. The image alone sets
`DEVCLAW_CHROMIUM_NO_SANDBOX` to it; CI, local runs and the committed Playwright
config never see it. The container is the boundary and only the project's own
test pages load.
And when the branch's whole diff against its base is documentation
(`is_doc_path`), the verify run's env carries `DEVCLAW_DOCS_ONLY=1`; the browser e2e is left out only if the
repository's existing script already honours it (a script without the guard still
runs the e2e — follow-up), and a missing base, an empty diff or a mixed one fails
closed (e2e stays in).

A green run is not yet a pass. The engine then checks that the first-run
manifest — `.devclaw/verify` and `.devclaw/workflow.md`, how the repo verifies
and how it plans — is TRACKED in the git index, not merely present in the
working tree. A repository whose `.gitignore` swallows `.devclaw/` would
otherwise deliver a branch carrying neither, and every later session would
re-derive from scratch with nothing able to notice. The untracked path is named
back to the session, which un-ignores and commits it.

### The branch ships through no-mistakes

The pipeline exists before the session does: the engine runs `no-mistakes init`
first thing for a code task that hands off (a session that finds no gate reads
the hand-off as impossible and ends `BLOCKED`; a pipeline that cannot start
fails the row before a session is spent), and its home (`NM_HOME`) is on the
`devclaw-state` volume so a redeploy keeps the gates
([runbook](./runbooks/no-mistakes-in-container.md)). After a green verify on a
branch its remote has not seen, the engine runs `no-mistakes init` again (it
starts the daemon under the host env, which keeps the GitHub credential: the
daemon pushes and opens the PR) and resumes
the SAME session with one hand-off prompt: write the contract to an intent
file, `no-mistakes axi run --intent-file … --skip ci --closes <issue>`, answer
each gate (fix, approve, or a reviewer question from the contract; anything
only the owner can settle ends `BLOCKED`). The session never pushes. CI is
skipped in the pipeline because the tick reads it: a red CI still stops the
goal with the failing log posted. The engine takes nobody's word for the
outcome: the branch's newest run must carry the session's head and have
passed, or the row is `INTERRUPTED`. "Carries" is read from `axi status`: the
head the run was submitted at while it is pre-push, else (once it has pushed or
finished, when status prints only the run's own `head_sha`) that head after a
`git fetch no-mistakes`, which must descend from the session's. A green verify
that leaves uncommitted changes and no commit is `INTERRUPTED` too that leaves
uncommitted changes and no commit. Fix commits the pipeline pushes are on
the remote branch the next session resets to. One session runs one pipeline,
so `DEVCLAW_MAX_CONCURRENT` (default 1) bounds no-mistakes load too.

### Failure is loud

A gate crash fails closed. A hand-off that did not ship fails the row. A
missing skill bundle refuses to run. An unreadable verdict never passes. A
world the host cannot read (`gh` down) does nothing and says so.

## The code map

```
devclaw/server/      tools/ (goals, projects, control, doctor) · routes/ (health, control, goals, projects, tasks, webhooks, me, console) · lifecycle.py
devclaw/goal/        tick.py · world.py · prompts.py · donegate.py · github.py · service.py · notify.py · self_deploy.py
devclaw/prompts/     session.md · done-gate.md
devclaw/task_queue.py · queue/settle.py · queue/admission.py
devclaw/engine/      claude.py · stub.py · workspace.py
devclaw/skills/      the worker-kind instructions the engine prepends (_common.md, _writes-code/, <kind>/, craft/)
devclaw/state_store/ core.py · schema.py · rows.py · control.py · events.py · goals.py · preferences.py
devclaw/loom/        limits.py · untrusted.py
devclaw/credentials.py · probes.py · config.py · project_registry.py · doctor/ · cli.py
frontend/            the console: an Angular PWA on @lifekit-hq/ui, built into devclaw/server/console_dist and served at /console
```
