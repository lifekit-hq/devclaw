# From a session to a merge

**One goal, one branch, one PR.** Every session of a goal works on
`goal/<goal_id>` in the goal's own checkout. The session ships the branch
itself through `no-mistakes axi run --skip ci` (`devclaw/engine/claude.py`):
the pipeline reviews, tests, documents and lints the change, pushes the branch
and opens the PR on the first run, then updates it on every later one; its
intent is the issue contract and `--closes` names the goal's issues. The engine
counts the hand-off only when the branch's newest run carries the session's
head and passed. Nothing merges mid-flight.

**The verdict of record is the project's CI on the delivered head**, read by
the tick from `gh pr view` (`devclaw/goal/github.py`). Red → the goal stops
with the failing job log on the thread; the session already ran what CI runs,
so a red CI is an environment gap, not a retry. Conflicting → a changed world;
the next session merges the base branch and resolves.

**The done-gate.** A session's `DONE` line is a proposal. With green CI the
tick spawns a read-only review session (`devclaw/prompts/done-gate.md`) over
the same head; the host validates its JSON (every clause satisfied with
evidence) and posts the verdict on the PR. A verdict counts only for the head
the review read; a PR that moved since gets a fresh review. Achieved →
`gh pr merge --squash --delete-branch --match-head-commit <the judged head>` →
the goal closes `achieved`, its checkout is removed, the owner is pinged once.
Refused or unreadable (including a review that changed its worktree) → the goal
stops with the findings; the owner answers with `decide` or a comment mentioning
the bot.
