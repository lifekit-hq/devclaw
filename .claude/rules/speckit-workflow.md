# Speckit workflow — how behavior-changing work starts on this repo

Ruled by Denys 2026-08-13: the homegrown proposal→ADR spec lifecycle is
**retired in full** (including the `invariant-guard` agent). The speckit
pipeline in `.specify/` is the only way behavior-changing work starts.
`docs/proposals/` and `docs/decisions/` are frozen history — read them,
never write there.

## The pipeline

```
/speckit-specify → /speckit-clarify → /speckit-plan → /speckit-tasks
→ implement (→ /speckit-analyze when artifacts disagree)
```

- **Every behavior-changing change starts with `/speckit-specify`** — a spec
  under `.specify/specs/`. No implementation before the clarify step is done.
- **`/speckit-clarify` is mandatory** and is run WITH Denys, one question at a
  time; answers are encoded back into the spec. This carries forward the old
  walk-the-`[OPEN]`s discipline — it was the highest-value anti-drift move and
  it survives the pipeline swap.
- **The spec is the direction memory.** Record rejected alternatives and their
  reasons in the spec itself; there is no separate proposal/ADR home anymore.
  A decision that exists only in conversation is the failure mode this whole
  discipline exists to prevent — that lesson predates speckit and outlives
  the old pipeline.
- **The constitution (`.specify/memory/constitution.md`) is the invariant
  statement** the pipeline checks specs against. A spec that requires an
  invariant change must say so explicitly and amend the constitution in the
  same arc. `CLAUDE.md` remains the repo contract; on conflict `CLAUDE.md`
  wins and the constitution is corrected in the same PR.
- **Slice, don't estimate** (kept from the old rule): break novel work into
  independently-shippable P1/P2/P3 increments, firm and size only P1 in
  devclaw's own units (N PRs, an end-of-week cap); leave P2/P3 named-unsized
  in the spec until P1 lands.

## Out of scope (existing rules apply, unchanged)

Bug fixes, incident response, mechanical refactors, docs/test-only changes,
and single bounded PRs Denys directly requests need no spec. The testing,
git-workflow, and cognition-prompts rules are untouched by this swap.

## What happened to the old pipeline (2026-08-13)

- `docs/proposals/` + `docs/decisions/` — frozen in place as historical
  record (INDEX marks them FROZEN). In-flight DRAFT proposals become speckit
  specs if/when their work is picked up.
- `.claude/rules/spec-lifecycle.md` — deleted (this file replaces it).
- `.claude/agents/invariant-guard.md` — deleted. Invariant enforcement =
  the constitution checked by the speckit pipeline + code review + the named
  regression tests (zero-token guard tests remain load-bearing: if one fails,
  the change is wrong, never the test).
