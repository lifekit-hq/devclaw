---
name: devclaw-evening
description: The devclaw evening loop in ONE command - what landed today, whether the VPS is actually running it, what runs tonight and whether anything will sit blocked until morning, the decisions Denys owes before he leaves, the ordered next actions for tomorrow, then park the session into the vault. Use whenever Denys ends the day on devclaw - "evening", "/devclaw-evening", "what's next", "what do we do tomorrow", "are we good for tonight", "wrap up devclaw" - or any time he would otherwise ask "what's next" and then "park it" as two nudges. Read-only on the instance; parks via the `park` skill.
---

# devclaw-evening - what landed, what runs tonight, what's next, park

Denys ends the day with the same two questions: *what's next* and *is
tonight going to run*. This skill answers both from ground truth and then
parks the session, so the morning starts from the vault card instead of
from a question. It is the mirror of `devclaw-morning`: morning turns
problems into one fix; evening turns the day into tomorrow's first line.

**Read-only on the instance.** It never steers, resumes, cancels, deploys,
or merges. A thing that must happen tonight and needs a human is a
DECISION line at the top, not an action this skill takes.

## Step 1 - What landed today (ground truth, never memory)

1. **Merged on devclaw** - `gh pr list -R lifekit-hq/devclaw --state merged
   --search "merged:>=<today>"` - number, title, and which spec or
   tinyspec each one closes (the branch name or the PR body says).
2. **Delivered by goals** - `get_scorecard_metrics` (window 24h): PRs
   opened / merged / open today, and the goals that closed.
3. **Is the VPS running it** - `curl -s <base>/health` (base = the MCP
   url minus `/mcp`) `git_sha` vs `git rev-parse --short origin/main`.
   If behind: name the merges it lacks and whether the sandbox image is
   among them (`runner/skills/` or `.sandcastle/` changed). Until the
   self-deploy arms on main moving, this is the one owner action that
   is not a decision and still has to be named - say it plainly as
   "deploy owed", never bury it.
4. **Session residue** - `git worktree list` and `git branch --list
   'feat/*' 'fix/*' 'docs/*' 'harden/*' 'refactor/*'` in this checkout:
   worktrees and branches the session opened that are merged (remove)
   or unmerged (name them in the card so they are not lost).

## Step 2 - Tonight (will the loop run, and on what)

1. **The schedule and the brakes** - `get_run_schedule` (window, tz
   Europe/Dublin, enabled), the operator hold, any usage pause
   (`get_loop_health` causes: `paused`, `window_closed`,
   `no_goal_armed`, `empty_backlog`).
2. **The lane** - `list_goals`: which goals are executing / queued per
   project, in what order, and the serial cap. A project with nothing
   armed idles all night - that is the `no_goal_armed` cause, owner
   bucket; say it now while there is time to arm one.
3. **What will sit blocked until morning** - every goal in `blocked`
   with a human-gated kind (`needs_answer`, `bug`, `lost_ref`,
   `dispatch_cap`, `merge_failed`) and every open Problem
   (`list_problems`) with its default and timebox. A Problem whose
   timebox expires tonight takes its default - say which, so a wrong
   default is a decision now, not a surprise tomorrow.
4. **Open devclaw PRs on red CI** - `gh pr list --state open` on the
   goal repos with checks; a red rollup is the worker's next correction
   (spec 032) and needs nobody, a red rollup on a gate-input file is
   the owner's (the change_class gate) - split them.

## Step 3 - Render (decisions at the top, then tomorrow)

```
# devclaw evening - <date>

## Before you go (<n>)              <- decisions only; plus "deploy owed" while that is still a button
- <goal / Problem> - <question, options, default, expires <when>> -> `<verb>`
- deploy owed: VPS <sha> is <n> merges behind main (<sandbox image: yes|no>)

## Landed today
- devclaw: #<n> <title> (<spec/tiny>) · #<n> ...
- goals: <m> PRs opened, <k> merged, <j> open · closed: <goal ids>

## Tonight
- window <hh:mm-hh:mm Europe/Dublin | 24/7> · hold <on|off> · pause <none | until hh:mm>
- lane: <project>: <goal> -> <goal> · <project>: NOTHING ARMED (idles all night)
- will sit until morning: <goal> (<kind>) · Problem <id> defaults to <option> at <hh:mm>

## Tomorrow, in order
1. <the first thing to do, with the number it moves>
2. ...

## Residue
- worktrees/branches: <name> (merged - remove | unmerged - <what it holds>)
```

Rules:

- **"Before you go" lists decisions only**, plus the deploy while it
  is still a button. Anything else the instance seems to want from a
  human is an instance of the owner-needed class: it goes into
  tomorrow's first line as "the class to fix", never as a to-do for
  tonight.
- **"Tomorrow, in order" is ranked by the number it moves**, against
  the failing axis on today's ratchet read; carry over the morning's
  chosen class if it did not land.
- **Every fact is read tonight**, never recited from the vault or from
  memory; the vault is where this skill WRITES.

## Step 4 - Park

Run the `park` skill: rewrite `~/memory/projects/devclaw/STATUS.md`
(where we parked / next actions = the "Tomorrow, in order" list above /
blocked on Denys = the "Before you go" list) and append one dated
distillate line to `~/memory/log.md`. The vault contract
(`~/memory/README.md`) wins on format; STATUS is a ~10-line card, not
a copy of this render.

## Why it's shaped this way

- **The evening question is really two** - "what's next" and "will it
  run" - and the second one is the one that costs a night when it is
  skipped (an unarmed project, a Problem defaulting wrong, a VPS on
  yesterday's code). Both are answered from the same reads.
- **Decisions only at the top** is the owner's ruling (2026-09-07): he
  acts on decisions; everything else the system asks of him is a defect
  and is filed as tomorrow's class, not tonight's chore.
- **Park is the last step, not a separate nudge**, because the morning
  skill reads the card this one writes; a day that ends without the
  card starts tomorrow with a question.
