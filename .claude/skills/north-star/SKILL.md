---
name: north-star
description: Judge a devclaw decision against the north star before it is built - a spec, a tinyspec, a PR, an issue, an idea in chat, a library or agent feature we are about to adopt. Eight tests (north-star axis, second-job, domain, class, weight, software-engineering, AI-engineering, bullshit) and one verdict - ADMIT / SHRINK / CUT / NOT-A-SPEC - written into the artifact, never only into chat. Use whenever Denys asks "does this align", "is this bullshit", "are we drifting", "judge this", "should we adopt X", "/north-star <thing>", when a spec is being specified or clarified, when /devclaw-morning recommends a fix, and inside /ship before a behavior-changing PR opens. The mechanical half (a filled North-star case exists before `gh pr create`) is the hook `.claude/hooks/north-star-case-guard.py`; this skill is the judgment half.
---

# north-star - is this moving us, or is it a second job?

The north star (`~/memory/projects/devclaw/plan.md`, restated 2026-09-06):
**devclaw runs 24/7 non-idle on planned work, nights come out clean, and it
self-heals through stops instead of waiting for the owner.** It fails in
exactly three ways - *stopped when it shouldn't*, *ran and produced
garbage*, *ran but needed the owner* - and the point of the whole project
is that devclaw becomes a helping hand, not a second job.

Drift is what happens between rulings: a change that is locally correct,
satisfies a metric, and composes into a system nobody designed. devclaw was
halved once for that (the 008 shrink; the four-brake deadlock of
2026-08-28). This skill is the judge that runs BEFORE a change is built.
It is deliberately adversarial: the strongest counter-argument first, the
verdict second. It never softens a CUT.

## Inputs

One thing to judge - a `specs/NNN-*/spec.md`, a `specs/tiny/*.md`, a PR
number, an issue, a described idea, or "we should adopt X" (a library, a
tool, an agent feature, a workflow). Read it fully. Then read, live:

- the current failing axis: `get_scorecard_metrics` (window 336h) ->
  `ratchet.checks` and `interventions` per verb; if the MCP is not wired,
  say so and use the last read recorded in `~/memory/projects/devclaw/STATUS.md`,
  dated;
- the constitution (`.specify/memory/constitution.md`) - the eight
  principles plus IX are the checklist, not a paraphrase of them;
- the mechanisms already at the boundary the change touches (grep the seam
  for kinds, brakes, classifiers, guards - count them).

## The eight tests

Answer each in one or two lines. A test that cannot be answered is a
finding, not a skip.

1. **North-star test.** Which ONE of the three failures does it move, which
   loop-health or scorecard number will show it, and what observation cuts
   it? (Constitution 2.9.0.) No answer -> not admitted, whatever else is
   true. Moving the number without moving the behaviour (re-counting,
   renaming, suppressing) fails this test, see 8.
2. **Second-job test.** After this ships, does the owner do MORE or LESS?
   List every owner action it adds (a button, a resume, a status read, a
   merge, a hand edit) and every one it removes. Only `decide`,
   `correct_implementation` and direction rulings are the owner's job
   (ruled 2026-09-07); anything else it adds is a defect it introduces.
3. **Domain test (IX).** Which of the five software domains does it live
   in - safety, money, state, the verdict of record, the protocol? Python
   outside them needs a reason by name. Is the gap being closed in order:
   a missing fact first, a missing instruction second, a brake last?
   Adopting standard practice, or inventing a devclaw-specific mechanism
   the standard one could give?
4. **Class test (VII).** Is this the class or the instance? Name the class
   in one sentence recognisable in another codebase. Count the mechanisms
   already at that boundary: two or more means the proposal must REPLACE
   them with one policy, never add a third. List the instance fixes a root
   fix would retire.
5. **Weight test.** Count the moving parts, special cases, new kinds, new
   env vars, new tables. Heaviness is a design smell, not a cost to pay
   (`~/memory/concepts/no-overengineering.md`). Is it reversible in one
   PR? Does it need a second consumer to justify generality it adds
   (N=2 trigger)?
6. **Software-engineering test.** Layer placement against the CLAUDE.md
   map (1 MCP, 2 goal, 3 cognition, 4 queue/engine, 5 worker) and the
   import direction; single writer (IV); fail-closed where consulted (V);
   loud failure over silent degradation (VI); model-agnostic worker layer
   (II); OAuth only (I). A change that reaches through a layer or adds a
   second writer fails here regardless of its metric.
7. **AI-engineering test.** Where does each decision in it live - an
   invariant in Python, a judgment in the model, a standard in the prompt
   (ruled 2026-08-08)? Instruct thin, verify thick: does it push knowledge
   about a project's code into devclaw, or supply facts and read the
   verdict? Prompt cost: every template line rides the least reliable call
   class (`claude --print`), so a line earns its place by changing
   behaviour, not by emphasis. Zero-token idle (III): any tick-path
   cognition firing on idle? Is it a cognitive guardrail compensating for
   the model (a classifier, a heuristic gate, a scaffold) - then it is a
   shed candidate and must name its A/B seam and instrument (VIII). Does it
   trust the input and verify the output, or rebuild a dossier the worker
   should pull itself? Is there an eval fixture, or is quality asserted?
8. **Bullshit test.** Ask: if this shipped and nothing else changed, what
   would we observe next week? A story whose only case is saving a round,
   adding a protocol, completing a spec's shape, satisfying the metric on
   paper, or making the codebase look more like a system is dropped first,
   not built last. Adopting a feature because a vendor shipped it, a repo
   has stars, or it "would be nice" is drift by definition - the question
   is always which failure it moves and what it costs the owner.

## For an adopted feature, library, tool or agent capability

Add three questions to the eight:

- Take the agent, never the vendor's control plane - does this replace a
  devclaw layer or extend it? (devclaw IS the control plane.)
- Would it survive the worker being a different ACP agent, or does it
  bind to one vendor's tool-wiring (II)?
- What does it cost in tokens per merged PR, and is that measured
  (`usage.tokens_per_merged_pr`)?

## Verdict

One of exactly four, with the reasons in the order the tests failed:

- **ADMIT** - all eight answered; write the North-star case into the
  artifact if it is not there yet.
- **SHRINK to <story>** - one story passes, the rest fail 1, 2 or 8; name
  what is parked (with a regrade date and owner - a label that stops a
  clock says what restarts it) and what is deleted. Spec 040 (2026-09-07)
  is the shape: three stories -> one tinyspec.
- **CUT** - fails 1, 2 or 8 outright, or 6/7 in a way no rewrite fixes.
  Say it plainly. Record it in the artifact's rejected alternatives, or in
  `~/memory/projects/devclaw/plan.md` if there is no artifact, so it does
  not come back next week as a fresh idea.
- **NOT-A-SPEC** - it is a ruling (goes in the plan/constitution), a fact
  (supply it), a tinyspec (route it), or a hotfix (ship it, then judge the
  class it belongs to).

Render:

```
# north-star verdict: <ADMIT | SHRINK to … | CUT | NOT-A-SPEC>  -  <thing>

axis: <stopped | garbage | owner> · number: <metric = value today> · cut when: <observation>
owner after: <fewer | same | MORE actions> - <what it adds / removes>
domain: <one of five | Python outside: <reason or none>> · order: <fact | instruction | brake>
class: <one sentence> · boundary: <seam>, <k> mechanisms there -> <replace | first>
weight: <parts, kinds, vars, tables> · reversible: <yes | no>
SE: <pass | fails: …> · AI-eng: <pass | fails: …>
bullshit test: <what we would observe next week>

verdict reasons, strongest first:
1. …
```

Then write the verdict where it lives: the artifact's `North-star case`
section and its rejected alternatives, or the plan. A verdict that exists
only in chat is the drift this skill exists to stop.

## Cut when (the judge's own case)

A judge that always admits is a rubber stamp, and this one grades its
author's proposals. Its number is derivable, no ledger: the `Status`
lines of `specs/*/spec.md` and `specs/tiny/*.md` and their North-star
cases. Over any ten consecutive artifacts judged, if no verdict was
SHRINK, CUT or NOT-A-SPEC, delete this skill - it is ceremony, and the
eight tests cost more than they catch. Denys reviews the verdict, not
the artifact: a verdict he overturns twice in a row is the same signal.

## Why it's shaped this way

- **Eight tests, not a score.** A score invites trading a failed test for
  a passed one; the north-star, second-job and bullshit tests are each
  sufficient to cut.
- **Live number, not remembered number.** The failing axis moves (on
  2026-09-07 it was "ran but needed the owner"); a judge arguing from last
  month's metric admits last month's fixes.
- **The verdict is written into the artifact** because the spec is the
  direction memory; the mechanical hook only checks that the section is
  filled, it cannot check that it is honest - that is this skill's job.
- **Adversarial by default** is how Denys works (`~/memory/CORE.md`): he
  does not want to be told he is right, and drift is what agreement
  compounds into.
