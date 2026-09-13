# From a session to a merge

**One goal, one branch, one PR.** Every session of a goal works on
`goal/<goal_id>` in the goal's own checkout. Delivery (`devclaw/delivery/`)
pushes the branch and opens the PR on the first delivery, then refreshes its
title and body on every later one (the title follows the latest increment's
commit subject; the body lists the increments landed). Nothing merges
mid-flight.

**The verdict of record is the project's CI on the delivered head**, read by
the tick from `gh pr view` (`devclaw/goal/github.py`). Red → the goal stops
with the failing job log on the thread; the sandbox already ran what CI runs,
so a red CI is an environment gap, not a retry. Conflicting → a changed world;
the next session merges the base branch and resolves.

**The done-gate.** A session's `DONE` line is a proposal. With green CI the
tick spawns a read-only review session (`devclaw/prompts/done-gate.md`) over
the same head; the host validates its JSON (every clause satisfied with
evidence) and posts the verdict on the PR. Achieved → `gh pr merge --squash
--delete-branch` → the goal closes `achieved`, its checkout is removed, the
owner is pinged once. Refused → the goal stops with the findings; the owner
answers with `decide` or a comment mentioning the bot.
