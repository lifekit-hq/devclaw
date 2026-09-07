---
name: devclaw-morning
description: The devclaw morning loop in ONE command - read the live instance, turn every problem into a CLASS (never an instance), pick the one class worth fixing today, route it to the right lane (fact / instruction / brake; tinyspec / spec), and hand Denys only the decisions that are his. Use whenever Denys starts the day on devclaw - "morning", "/devclaw-morning", "what's the status and what do we fix", "what are the core problems", "what's systemic", "where are we" on devclaw - or any time he would otherwise ask status, then problems, then "find the class", then "fix it" as four separate nudges. Read-only on the instance; it proposes, Denys decides, and only then does implementation start.
---

# devclaw-morning - status, class, one fix, your decisions

Denys runs the same loop every morning by hand: *what's the status* -> *what
are the problems* -> *what is the CLASS behind them* -> *fix the class* ->
*what needs me*. This skill is that loop as one command, so he types one
thing and reads one page. The doctrine it encodes is `CLAUDE.md` ("Design
doctrine - systemic over specific") and the north star it measures against
is the one in `~/memory/projects/devclaw/plan.md`: devclaw runs 24/7
non-idle on planned work, nights come out clean, and it self-heals through
stops instead of waiting for the owner. It fails in exactly three ways -
*stopped when it shouldn't*, *ran and produced garbage*, *ran but needed the
owner* - and every problem this skill names is filed under one of them.

**Read-only.** This skill inspects and proposes. It never steers, resumes,
cancels, deploys, or commits. Implementation starts only after Denys says
which class to fix.

## Step 1 - Read the instance (the facts)

Everything comes from the devclaw MCP pointed at the VPS. If `list_goals`
is not in the toolset, say so and stop - the hard rule and the exact
wording are in `.claude/skills/devclaw-status/SKILL.md`; never read the
local `devclaw.db`.

1. **Goal spine + needs-you + cycle history** - run the `devclaw-status`
   procedure (that skill is the status read; do not re-implement it).
2. **Why the loop is or isn't running** - `get_loop_health` (spec 039):
   the cause per idle stretch (`no_goal_armed`, `paused`, `window_closed`,
   `empty_backlog`, a `mechanical:*` block, ...) and the responsibility
   bucket each derives to (devclaw / owner / project / provider).
3. **The owner-needed number, per verb** - `get_scorecard_metrics`
   (window 336h): `interventions` split into decisions / resumes / steers /
   non-worker commits, `convergence.first_pass_rate`, `rounds_median`,
   the evaluator verdict split, and the ratchet block.
4. **The problems catalog** - `list_problems`, window by `last_seen`
   (the `count` is lifetime).
5. **Deploy lag** - `curl -s <base>/health` (base = the MCP url minus
   `/mcp`) for `git_sha`, against `git rev-parse --short origin/main`.
   A VPS behind main means last night measured the previous system;
   say so before any number is read.
6. **Open devclaw-authored PRs** - `gh pr list -R lifekit-hq/<repo>
   --state open` on the repos the goals point at, so a PR waiting on a
   human merge or a red CI rollup is a fact, not a surprise.

## Step 2 - Turn instances into classes (the judgment)

Every item from step 1 - a blocked goal, a wedge, a catalog line, a
red PR, a resume Denys typed, a hand commit on a goal branch, an idle
stretch with a cause - is an INSTANCE. The output of this step is the
list of CLASSES, and the rule is the `root-cause` skill's: two instances
with different surfaces and one mechanism are one class; a fix that only
unwedges today's case is a smell.

For each class, fill exactly these five lines:

- **Class** - the shape, stated so it would be recognisable in another
  codebase ("a stop whose only exit is a human typing retry", "the
  verdict is not delivered to the actor", "two writers to one state").
- **Axis** - which of the three north-star failures it belongs to.
- **Boundary + count** - the seam it lives at (`file` or layer), and how
  many mechanisms ALREADY sit at that boundary (grep for the brakes,
  guards, kinds, classifiers there). Two or more is a design signal:
  the proposal must be "replace with one policy", never "add a third"
  (`~/memory/concepts/no-overengineering.md`, the 2026-08-28 deadlock).
- **Instances** - the items from step 1 that belong to it, with goal ids
  / PR numbers / timestamps. This is the evidence, and the list of
  band-aids a root fix retires.
- **Number** - the loop-health or scorecard read that will move when it
  is fixed, with today's value.

Do not pad. A morning with one class is a good morning. A morning with
zero classes says "nothing systemic; the instance is doing its job" and
stops here.

## Step 3 - Pick ONE and route it (the recommendation)

Rank the classes by the number they move against the failing axis
(on 2026-09-07 the failing metric was "ran but needed the owner":
interventions per achieved goal; check the ratchet block for today's).
Recommend ONE class to fix today. Say why the others wait.

Route the fix before proposing it, in constitution IX's order:

1. **A missing fact** (an environment or tool the worker lacks) - supply
   it; no code.
2. **A missing instruction** (one line in a `runner/skills/` file,
   checked by an eval) - write the line.
3. **A software brake** - only inside the five domains devclaw owns
   (safety, money, state, the verdict of record, the protocol), and
   only if 1 and 2 cannot close it.

Then size the lane (`.claude/rules/speckit-workflow.md`): a bug fix,
an incident, or one bounded PR is a **tinyspec** (`specs/tiny/<name>.md`);
anything touching an invariant, a layer boundary, or more than a
handful of files is a **spec** (`/speckit-specify` -> `/speckit-clarify`
with Denys). Either way the artifact states its north-star case first
(constitution 2.9.0): the failure moved, the number, the cut condition.

Before rendering, run the `north-star` skill on the recommended fix. A
verdict of CUT or NOT-A-SPEC changes the recommendation, not the render.

## Step 4 - Render (one page, decisions at the top)

```
# devclaw morning - <date>

## Your decisions (<n>)          <- ONLY decisions: decide / correct_implementation / cancel / a ruling
- <goal or class> - <the question, the options, the default> -> `<verb>`

## Instance
- VPS <sha> vs main <sha> - <current | BEHIND by n merges: last night measured the old system>
- Last night: <clean | wedged: class x n> · clean rate <a>/<b> · loop idle causes: <cause x n, bucket>
- 14d: first-pass <x> (<n>/<m>) · rounds median <r> · interventions/goal <i> = decisions <d> · resumes <r> · steers <s> · hand commits <c>

## Classes (<n>)
1. <Class> - axis: <stopped | garbage | owner> - boundary: <seam>, <k> mechanisms there
   instances: <ids, PRs, times> · number: <metric> = <value>
2. ...

## Today's fix: <class 1>
- Route: <fact | instruction | brake> · lane: <tinyspec | spec> · retires: <the instance fixes it deletes>
- Why not the others: <one line each>

## Needs nothing from you
- <running / quiet goals, collapsed>
```

Rules that keep it honest:

- **"Your decisions" lists decisions only.** A resume on a mechanical
  condition, a deploy button, a hand merge, a hand commit are not
  decisions - they are instances of the owner-needed class and go in
  the Classes table as evidence, never in the decisions list.
- **Numbers come from the tools, verbatim.** No estimate, no "roughly".
  A tool that is unreachable is reported as unreachable.
- **Stop after rendering.** Denys picks. On his word the next step is
  `/root-cause` on the chosen class, then the tinyspec or spec, then
  implementation, then `/ship`. Never start the fix from inside this
  skill.

## Cut when

Two weeks of mornings in which Denys still asks "what are the problems"
or "what is the class" AFTER the render - the skill did not replace the
nudges, it added a page. Or the Classes table names the same class three
mornings running with no fix landing - then the skill is a report, and
the fix belongs to a spec, not to a re-read.

## Why it's shaped this way

- **One command replaces four nudges.** The nudges were the same every
  day; the judgment inside them (instance -> class -> one fix) is the
  part worth writing down, so it is the body of this skill.
- **Classes, never instances**, because devclaw was halved once for the
  pile that instance fixes become. The boundary count is the tripwire:
  it makes "add a third brake" visible before it is proposed.
- **Decisions only at the top**, because that is the owner's ruling
  (2026-09-07): he acts on decisions; everything else he is asked to do
  is a defect, and the skill files it as one instead of asking.
- **Read-only**, because a status read that mutates is how a "status"
  cron restarted the server mid-run (the retired ops-agent).
