# Phase 0 Research — Speckit Execution Substrate (P1)

Decisions that resolve the design unknowns for the MVP. Each is
Decision / Rationale / Alternatives, grounded in code read this session.

## D1 — How the worker runs speckit, model-agnostically (Principle II)

**Decision**: The worker (layer 5, `openhands-runner`) runs the speckit flow using
(a) the repo's `.specify/scripts/bash/*.sh` (create-new-feature, setup-plan,
setup-tasks, check-prerequisites) — these are already agent-agnostic bash — and
(b) the speckit **command prompt-content vendored as plain-markdown worker skills**
(the `/specify`, `/plan`, `/tasks`, `/implement` step instructions), discovered the
devclaw way (`ls .agent/skills/` + `cat`). The `_advance_brief` tells the worker
*which feature* to advance and to run the speckit steps; the worker fills the
templates with its own turns.

**Rationale**: Honors Principle II — no Claude-Code slash-command wiring, no
vendor-specific frontmatter. Bash + markdown is what survives an `ACPAgent` swap.
Mirrors the existing worker-skill model.

**Alternatives rejected**: (a) Depend on Claude-Code `.claude/commands/*` slash
commands — vendor-locks the worker, violates II. (b) Run speckit *on the host*
(layer 3) — re-creates the host cognition chain we are removing, and re-introduces
the OOM class (single `claude --print` emitting a whole plan). The worker has the
repo and iterates; the host does not.

## D2 — Where the slice-guard finds `tasks.md` (FR-005)

**Decision**: The guard globs **all** `specs/*/tasks.md` and sums unchecked→checked
(`- [ ]`→`- [x]`) flips between `HEAD^` and `HEAD` (via `git show <ref>:<path>`),
exactly as it does for `PLAN.md` today but across the per-feature files. It does
**not** rely on `.specify/feature.json` to locate the active feature.

**Rationale**: `feature.json` is **gitignored** (confirmed: `.specify/.gitignore`)
so it is *not* readable at a git ref — `git show HEAD:.specify/feature.json` fails.
Globbing needs no pointer, is a pure git subprocess (zero-token, settle-time), and
the build-ahead threshold (`> N` flips in one increment) generalizes unchanged.

**Alternatives rejected**: (a) Read the active feature from `feature.json` — not
git-readable at a ref. (b) Thread the active feature dir from dispatch into the
guard — added coupling for no gain; the sum-across-all behavior is equivalent for
build-ahead detection.

## D3 — Fail-closed vs fail-open, precisely (Principle V)

**Decision**: Keep today's split. **Detection** of build-ahead is best-effort /
fail-OPEN (a git hiccup ⇒ 0 flips ⇒ no false wedge) — unchanged from
`slice_guard.mega_dump_flips_sync`. The **task gate** that consumes it stays
fail-CLOSED under `strict`. The change is only the *source file*
(`PLAN.md`→`tasks.md`), not the consequence. This matches the constitution's
Principle V wording and FR-005.

**Rationale**: FR-005 says "read tasks.md, keep fail-closed." The existing
detection is deliberately fail-open (avoids wedging on a git blip); the gate is
where closed-ness lives. Preserving the split avoids regressing #186.

## D4 — Backward-compatibility during transition (US4 is P2)

**Decision**: The guard reads `tasks.md` flips; if **no** `specs/*/tasks.md` exists
at all, it falls back to the legacy `PLAN.md` reader. The fallback is removed by
the shrink slice / US4 migration, not in P1.

**Rationale**: US2 makes new repos speckit, but existing `PLAN.md`-spine repos
aren't migrated until US4 (P2). A hard cutover in P1 would wedge in-flight goals on
un-migrated repos. Dual-read is the loud-safe transition (Principle VI).

**Alternatives rejected**: Hard cutover in P1 — breaks un-migrated repos before US4
lands. Silent no-op when neither file exists — that would disable the guard
silently (violates VI); instead, absent-both ⇒ the existing fail-open-on-detection
path (0 flips), which is the current behavior for an absent `PLAN.md`.

## D5 — Adopt vs install detection (US2, FR-002/003)

**Decision**: In the `onboard` tool (`devclaw/server/tools.py`): if the repo has a
committed `.specify/` directory ⇒ **adopt** (record it; write **no** `PLAN.md`). If
absent ⇒ **install** speckit by generating the `.specify/` scaffold and opening a
**reviewable PR** through the existing delivery path (`delivery/`), never a direct
commit to the default branch. A repo whose install PR is unmerged is **not** run
for feature work (no half-installed state — spec Edge Case).

**Rationale**: Presence of `.specify/` is the unambiguous, git-readable adopt
signal. Reusing the delivery PR path keeps "reviewable, never silent" (Principle
VI) with no new delivery code.

**Alternatives rejected**: Detect via `feature.json` (gitignored, unreliable);
silently `git add .specify && commit` (violates FR-003/VI).

## D6 — Done-gate grounding on the spec (FR-006)

**Decision**: `evaluator.py`'s `review_repository` grounds on the executing
feature's `specs/NNN-*/spec.md` **Success Criteria + Requirements** as the
`done_when`, instead of a host-side `firmed done_when`. For P1 the feature dir is
the one the goal is executing (recorded at dispatch); the evaluator reads
`spec.md` from the repo (it already reads repo files). A non-achievable/ambiguous
spec ⇒ bounce to needs-human (unchanged mechanism).

**Rationale**: The spec is now the contract of record; grounding the gate on it
keeps "done is a proposal, gated on grounded evaluation" (Principle V) without a
host firming step. Minimal rewire — the evaluator already does repo review.

**Alternatives rejected**: Keep grounding on `firmed done_when` — that keeps the
host firming chain alive (defeats the arc) and can drift from the spec.

## D7 — Feature/spec creation under autonomous execution (FR-008)

**Decision**: The worker runs `create-new-feature.sh` (auto-numbers `specs/NNN-*/`)
and the mechanical speckit steps **without** an interactive `/clarify` checkpoint.
Ambiguity is caught upstream by the **shipped 006 intake-readiness gate**
(`devclaw-ready` vs `needs-refinement`) and, if a spec still can't be firmed,
bounces to needs-human. Async-clarify is a deferred slice.

**Rationale**: Matches spec FR-008 + the 006 doorway that already grades asks.
Keeps autonomous runs unblocked while preserving the ambiguity backstop.

## D8 — Zero-token idle preserved (Principle III)

**Decision**: No new work on the idle/blocked tick path. slice-guard `tasks.md`
read is a settle-time git subprocess (post-session, work-present). adopt/install
detection fires at onboard/dispatch. The `_advance_brief` change is prompt-text
only (no new host cognition). The `FakeClaude.calls == 0` idle/blocked tests must
stay green — a new test asserts the speckit brief change adds no idle-path call.

**Rationale**: The whole quota guarantee rides on this. The changes are all on
the work-present path, so the guard holds.

---

**All NEEDS CLARIFICATION resolved.** No open unknowns block Phase 1.
