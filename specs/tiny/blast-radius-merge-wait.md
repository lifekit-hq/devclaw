# TinySpec: a hard-to-reverse goal waits for the owner's `merge`

**Branch**: fm/dc-979-blast-radius
**Date**: 2026-11-06
**Status**: in review
**Complexity**: small (one rule module, one branch in the merge edge, one PR read field)
**Issue**: #979 — DevClaw sends migrations and deletions through the same gate as a copy fix

## North-star case

- **Failure moved**: *ran but needed the owner* — but only where it should. A
  wrong migration is expensive, a wrong copy change is a revert; today both
  squash-merge the moment the done-gate passes. The owner acts on a decision
  (`decide`), never on a chore, so this adds one decision and no new verb.
- **Number that shows it**: unattended merges of a change whose Done-when or
  paths name a migration, deletion or production-data write: today every one;
  after, zero (`tests/test_goal_tick.py::test_a_hard_to_reverse_change_never_merges_without_the_owners_merge`).
  Reversible goals merge as before (`test_an_achieved_verdict_merges_and_closes`).
- **Cut when**: the owner answers `merge` on nearly every gated goal for a
  month (the keyword rule is too broad), or a hard-to-reverse change merges
  unattended anyway.

## What

- `devclaw/goal/blast.py`: `classify(done_when, files)` returns `hard` / `wide`
  / `reversible` by regex over the `## Done when` section and the PR's paths
  — no model call. `is_merge_decision` accepts a comment whose whole text is
  `merge` / `decide: merge` after the mention.
- `tick._merge`: before the squash-merge, a `hard` goal posts one `block`
  record (`why=hard_to_reverse`, the judged head) and returns; it merges only
  when a comment mentioning the bot, newer than that record, answers `merge`.
  A moved head is a new head and asks again.
- `github.PrFacts.files`: the PR's changed paths, read in the existing
  `gh pr view` call.

## Decisions

- Classified at the merge edge from the live contract and the live PR files,
  not stored "on intake": the PR's files do not exist at intake, and the host
  holds no derived state (pillar 5).
- `wide` is classified but not yet gated: it proceeds as `reversible` today;
  the issue names the class and gives it no behaviour.
- The release comes from the repository owner only (`author_association` is
  `OWNER`, or the author is the repo's owner login); any other or missing
  author leaves the block standing. No config surface.
- Fail closed on the answer: anything but the bare word `merge` (`do not
  merge`, `merge it later`) leaves the goal waiting.

## Done when

- A `hard` goal with an achieved verdict never calls the merge without a
  newer `merge` answer; it posts one block record and one ping.
- A `reversible` goal merges as it did.
- `tests/test_goal_layer_stays_readable.py` is green.
