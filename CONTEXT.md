# devclaw
An autonomous delivery host behind MCP: you hand it GitHub issues, it spawns `claude -p` sessions in a per-goal worktree, lands one reviewable pull request, and judges "done" mechanically. The glossary covers the goal, session and verdict vocabulary of the host, its tick rule and its owner-facing channel.

## Language

**Goal**:
The unit of work devclaw supervises: one or more GitHub issues (each labelled `ready`, read live), a display-only objective (no path decides on it; the console shows the issue's live title) and a branch `goal/<id>`, stored as a row in `goals` and closed with an outcome (achieved, cancelled, ...). One goal yields exactly one PR.

**Project**:
A registered repository (`register_project`) that goals run against. At most one goal per project has a live session at a time.

**Contract**:
The GitHub issue itself: its `## Done when` / `## Acceptance` section is what the done-gate judges, read live. The host never grades or rewrites it.

**Tick**:
One pass of the tick rule over every open goal, every `DEVCLAW_GOAL_TICK_SECONDS` (default 15 min) and at once when a session settles or a webhook arrives. A tick spawns a session only when the world moved.

**World**:
What the host reads from GitHub each tick: the goal's PR (head, state, mergeability, CI rollup), the issues, every comment on the issue and PR, and which credentials probe green. Only the goal layer reads it.

**Fingerprint**:
The compact observation of the world (PR state, head sha, CI state, newest instruction and devclaw record ids, green credentials, issue states) stored as the goal's `last_seen`. Unchanged since the last session was given it means zero tokens spent.

**Session**:
One run of the `claude -p` agent in the goal's worktree against the world as facts. It ends with exactly one exit line.

**Task**:
The queue row that records one session (`tasks` table, kind `implement_feature`, `fix_bug` or `review_repository`). The execution record; only the TaskQueue mutates it.

**Exit**:
The one line a session ends with: `DELIVERED`, `DONE`, `BLOCKED` or `NOTHING`, plus the host-assigned `INTERRUPTED` (silence, red local verify, hand-off that did not ship) and `REVIEW` (a review session's result).

**Review session**:
A fresh, read-only session (`review_repository`) over the delivered head that decomposes the contract into clauses and answers in JSON. It is failed if it moves HEAD or changes a tracked file.

**Done-gate**:
The mechanical check that decides a goal is achieved: CI green on the delivered head plus a valid review verdict. Done is never the agent's word.

**Verdict**:
The review session's validated JSON: achieved only when every clause is satisfied with evidence and no question is open; unreadable JSON is `unreadable` and never passes. Posted on the PR before a squash-merge.

**Decision**:
The owner's answer on a goal, recorded in `decisions` and posted as a comment mentioning the bot (`@devclaw`, `DEVCLAW_MENTION`) via the `decide` tool. Its `comment_url` is the record; the stored text is a display-only copy, and the console shows the comment's live body. It is the owner's one channel: the owner acts only on decisions.

**Record**:
A marked comment devclaw writes on a thread, `<!-- devclaw:<kind> k=v -->`, of kind `block`, `verdict`, `scope` or `intake`. A record is a fact, never an instruction.

**Block**:
A record saying why a goal stopped (session blocked, red CI after a green verify, `hard_to_reverse`, DONE without a PR) with its question. A blocked goal releases its project lane; only a newer owner comment or `decide` wakes it.

**Scope gate**:
The check before review that every file in the PR's diff is named by the goal's issues or owner comments. An unnamed file posts a `scope` record and spawns one correction session; a second excess stops the goal with `SCOPE_EXCEEDED`.

**Hard-to-reverse**:
The merge-edge classification (`hard` / `wide` / `reversible`, `devclaw/goal/blast.py`) computed live from the issues' text and changed paths. Only `hard` changes the outcome: the merge waits for the owner to answer `merge`.

**Worktree**:
The treehouse worktree leased to a goal at its first spawn and returned on close. It isolates one goal's branch from another's; it is not a sandbox.
_Avoid_: sandbox, clone (the earlier design used per-task docker sandboxes and `.goals/<id>` clones)

**Verify**:
`.devclaw/verify`, the script the session derives from the repository's own CI so the worktree runs what CI runs before the session ends. A red run goes back to the same session over `--resume`, up to `DEVCLAW_VERIFY_ROUNDS`.

**Workflow manifest**:
The first-run files `.devclaw/verify` and `.devclaw/workflow.md` (how the repo verifies, plans, builds and ships), derived from the repository and required to be tracked in git. devclaw bakes in no planning scaffold of its own.

**Usage pause**:
The account-wide pause set when a quota, auth or provider-outage failure is classified (`devclaw/loom/limits.py`): work is snapshotted and requeued, cleared with `clear_usage_pause` or by backoff.

**Operator hold**:
A manual dispatch stop (`set_operator_hold`), stored in `meta` beside the run window (`set_run_schedule`) and the dial (`set_max_concurrent`); while a hold or an outside-window time applies, the queue dispatches no new session, though ticks still run and submit tasks that wait.

**Intake**:
`file_intake`: files, or counts onto, a GitHub issue in a registered project by repo plus fingerprint, returning a receipt `{id, url, state}`. It starts no goal and changes no code.

**Console**:
The Angular PWA served at `/console` that reads the same state as the MCP tools over HTTP; its per-user appearance is the one thing the host stores for it.

## Relationships
- A **Project** has many **Goals**; a **Goal** targets one or more issues (its **Contract**) and ends in exactly one PR on `goal/<id>`.
- Each **Tick** reads the **World**, compares its **Fingerprint** with the goal's `last_seen`, and spawns a **Session** only if it differs.
- A **Session** is recorded as a **Task**, runs in the goal's **Worktree**, runs **Verify**, and ends with an **Exit**.
- A `DONE` **Exit** with green CI passes the **Scope gate**, then a **Review session** whose **Verdict** the **Done-gate** validates; an achieved verdict squash-merges and closes the **Goal**.
- A failed gate or a `BLOCKED` **Exit** posts a **Block** **Record**; only a **Decision** (or a newer owner comment) wakes the goal.
- A **Usage pause** or **Operator hold** stops the queue from dispatching any new **Session**; **Ticks** still run and the **Goal** rows are untouched.

## Flagged ambiguities
- "Session" (README, AGENTS.md "One prompt", `docs/architecture.md` "One session") and "task" (`devclaw/state_store/rows.py:11` `TaskKind`, `tasks` table) name the same run at different layers.
- "Fingerprint" is the world observation in `devclaw/goal/world.py` / `last_seen`, and also the idempotency key of an intake (`devclaw/server/tools/intake.py:35` `derive_fingerprint`).
- "Verdict" is the review JSON (`devclaw/goal/donegate.py`) and also the doctor's check result enum `ok/warn/fail/unknown` (`devclaw/doctor/model.py:16`).
- "Blocked" is a goal stopped on a Block record (`devclaw/goal/tick.py`) and, in open issue #1039, an issue "blocked by an open issue" in the picker; those are different.
- "Owner" (the one channel, `docs/architecture.md`) versus "operator" (`set_operator_hold`, `README.md` tools list): the same person is called both.

