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
  under `specs/` at the repo ROOT — not under the .specify directory, which
  holds only the scripts, templates and constitution the pipeline runs on.
  No implementation before the clarify step is done.
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
- **Slice for reviewability, never for scope** (ruled by Denys 2026-08-22,
  replacing "slice, don't estimate — firm only P1"). Break novel work into
  independently-shippable P1/P2/P3 increments so each lands as ONE coherent,
  reviewable PR. The increment is the unit of REVIEW. It is not the unit of
  commitment — **the whole spec is the commitment.**
  - **P1 landing is not a stopping point.** Keep going until every user story
    in the spec is built, or say out loud which are dropped and why. A spec
    left carrying "SPECIFIED, NOT IMPLEMENTED" is unfinished work with no
    owner, not a deferral — that is how 007, 008, 010 and 012 each ended up
    part-built with nothing tracking the remainder.
  - **When a goal drives the work, `done_when` covers the WHOLE spec**, so the
    done-gate keeps chaining increments until the scope is met instead of
    closing after the first one. Scoping `done_when` to P1 is what made the
    autonomous path stop; the chaining mechanism already exists and is
    progress-aware (a successful dispatch is refunded against the cap), so a
    healthy full-scope goal runs to completion.
  - What survives from the old rule: still don't estimate in human-scale time,
    and still don't over-design P3 before P1 teaches you something. If P1's
    outcome changes what P2/P3 should be, **revise the spec and keep going** —
    revision is not a reason to stop.

## Below spec size: the tiny lane (adopted 2026-08-24, issue #668)

Bug fixes, incident response, mechanical refactors, and single bounded PRs
Denys directly requests do NOT enter the full pipeline — they run through the
**tinyspec lane** instead: `/speckit-tinyspec-tinyspec` writes ONE file at
`specs/tiny/<name>.md` (What / Context / Requirements / Plan / Tasks /
Done-When), committed with the change, and
`/speckit-tinyspec-implement` executes it. `specs/tiny/` sits outside the
`NNN-` sequential numbering on purpose. This replaces the old "needs no spec"
exemption: small work now leaves direction memory too, at one file of
overhead. When in doubt whether work is tiny or spec-sized,
`/speckit-tinyspec-classify` routes it — and anything touching an invariant,
a layer boundary, or more than a handful of files is NOT tiny.

Docs-only and test-only changes still need no artifact at all. The tinyspec
extension is vendored at `.specify/extensions/tinyspec/` (v1.0.0, pinned,
content-vetted; installed via `specify extension add --from <v1.0.0 zip>` —
the community catalog stays discovery-only). The worker-side lane (routing
small in-sandbox dispatches through tinyspec instead of full speckit) is the
remaining half of #668 and is NOT yet adopted.

The testing, git-workflow, and cognition-prompts rules are untouched.

## What happened to the old pipeline (2026-08-13)

- `docs/proposals/` + `docs/decisions/` — frozen in place as historical
  record (INDEX marks them FROZEN). In-flight DRAFT proposals become speckit
  specs if/when their work is picked up.
- `.claude/rules/spec-lifecycle.md` — deleted (this file replaces it).
- `.claude/agents/invariant-guard.md` — deleted. Invariant enforcement =
  the constitution checked by the speckit pipeline + code review + the named
  regression tests (zero-token guard tests remain load-bearing: if one fails,
  the change is wrong, never the test).
