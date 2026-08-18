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

# Phase 0 Research — US3 label-routed ceremony tiers (P2 increment, 2026-08-18)

Grounded this session: `specify` CLI v0.16.3 probed live; MartyBonacci/spec-kit-extensions
read file-by-file via the GitHub API; catalog contents searched.

## D9 — The tier ladder's middle tiers are ADOPTED, not built (FR-009)

**Decision**: Vendor (copy + pin) the `bugfix` and `hotfix` workflows from
**MartyBonacci/spec-kit-extensions** (MIT, 83★, last push 2025-10-12 — dormant,
which is *for* pinning, not against it) into devclaw's packed speckit harness.
Only those two workflows; `modify`/`refactor`/`deprecate` are out of scope.
Verified facts about the pack:

- Commands are **plain markdown** (`commands/speckit.bugfix.md`, `speckit.hotfix.md`)
  + **plain bash** (`scripts/create-bugfix.sh`, `create-hotfix.sh`) — agent-agnostic,
  Principle II-compatible.
- `bugfix` creates `specs/bugfix-NNN-<slug>/` with a bug-report artifact, then
  chains into the standard `/speckit.plan → tasks → implement` steps with a
  **regression-test-before-fix** discipline. Its `bugfix-NNN` counter is a
  separate number-space from feature `NNN` (globs `specs/bugfix-*`), so it does
  NOT worsen the #553 numbering collision.
- `hotfix` creates an incident-log artifact with severity assessment and an
  expedited plan step, plus a mandatory post-mortem section.
- Because the tier dirs live under `specs/` and carry `tasks.md`, the P1
  slice-guard (`specs/*/tasks.md` glob, D2) picks them up **with zero changes**.

**Rationale**: The community pack is exactly Denys's requested middle tier
(real-but-lightweight); authoring our own duplicates it and violates the
2026-08-15 adopt-not-build ruling on #534.

**Alternatives rejected**: (a) Author devclaw-native bugfix/hotfix templates —
re-litigates adopt-not-build. (b) Live dependency on the upstream repo —
dormant 10 months, unreviewed updates entering an autonomous loop; pin + vendor
is the loud-safe form. (c) Install from a workflow catalog — verified 2026-08-18:
neither the official nor the community catalog carries any bugfix/hotfix tier
(3 full-cycle workflows only), so there is nothing to install from.

## D10 — Routing: host-side mechanical, ambiguity only routes UP

**Decision**: The label→tier route is decided **mechanically at dispatch time**
(zero LLM): (a) companion path — `dispatch_task`'s existing `kind` is the signal
(`fix_bug` → bugfix tier; `implement_feature` → full cycle); (b) goal path — when
the advance target is a labeled issue, the labels fetched at dispatch (existing
mechanical `gh` call site) map `feature|enhancement → full`, `bug → bugfix`,
`critical-fix|hotfix → hotfix`, `chore|docs → direct`. The chosen tier is stamped
into the advance/dispatch brief as the workflow the worker runs. Ambiguous or
absent signal → **full cycle** (the careful path); nothing ever silently routes
to a *lighter* tier (spec Edge Case + FR-004). The worker does not re-decide the
tier; it executes the stamped one.

**Rationale**: Routing is a lookup, not judgment — Principle III (zero idle
cognition) and the SDLC rule "put each decision where it can be enforced"
(invariant → python). Stamping in the brief keeps the worker model-agnostic.

**Alternatives rejected**: (a) Worker decides the tier from the issue — moves an
enforceable invariant into prompt-space; a worker rationalizing "this is just a
bug" onto a feature is exactly the #358 integrity class. (b) LLM-classified
routing — burns tokens on a lookup and violates the zero-token dispatch shape.

## D11 — Registration rides speckit's own mechanism (FR-009), v0.16.3 verified

**Decision**: The vendored workflows register through speckit's native workflow
system: `specify workflow add <local path>` (v0.16.3 CLI verified 2026-08-18:
`workflow add` installs from catalog, URL, **or local path**; `list/info/enable/
disable/resolve` all present). The packed harness (what `speckit_setup.py`
scaffolds into a repo) carries the vendored pack under `.specify/extensions/`
+ the two command markdowns as plain worker-skill content, mirroring D1's
vendoring of the core commands. No devclaw-side workflow abstraction is built.

**Rationale**: FR-009 verbatim. The catalog *mechanism* is the delivery vehicle;
the vendored copy is the source (D9c: catalogs carry no tiers).

## D12 — Vendored scripts must NOT own branching (goal-branch delivery wins)

**Decision**: The pack's `create-bugfix.sh`/`create-hotfix.sh` create and check
out their own `bugfix/NNN-*` branches. In devclaw execution, **delivery owns
branches** (goal-branch accumulation #486; target_branch contract). The vendored
copies are patched — branch creation removed behind an env guard
(`SPECKIT_NO_BRANCH=1`, set in the worker environment), artifact creation kept —
and the patch is documented in the vendor README inside the pack dir. This is
the ONLY delta from upstream; everything else is byte-verbatim at the pinned
commit.

**Rationale**: Two branch-owners is a mechanism collision (the class, not the
instance: any vendored tool that mutates git state must be subordinated to the
delivery layer). A documented single-purpose patch beats wrapper indirection.

## D13 — SC-005 refined to tier-appropriate artifacts

**Decision**: Bug-tier work produces at most its lightweight `specs/bugfix-*/`
set (this is the point of the middle tier); chore/docs produce zero artifact
dirs; only feature/enhancement produce full `specs/NNN-*/` sets. Encoded in the
spec (SC-005, US3 scenarios) this session — sourced from Denys's 2026-08-15
tier-ladder refinement recorded on issue #534, not a new decision.

---

**All NEEDS CLARIFICATION resolved.** No open unknowns block Phase 1 for US3.

## Open questions — RESOLVED 2026-08-18 (Denys: "lets go"; file-level verification same session)

1. **`hotfix` kind on `dispatch_task`?** → **No.** The hotfix tier is
   issue-label-only (`critical-fix`/`hotfix`); companion-path hotfixes are rare
   and human-in-the-loop there anyway. `dispatch_task`'s kind enum is unchanged.
2. **Bugfix-tier source** → **D9 STANDS (MartyBonacci, vendored frozen).**
   The survey surfaced Quratulain-bilal/spec-kit-bugfix (modern extension
   format, active 2026-07) as an alternative, but file-level verification
   killed the swap: her extension is **feature-scoped** — `.report` writes
   `specs/{feature}/bugs/BUG-NNN.md` inside an existing feature dir located
   "by branch name or most recently modified" and `.patch` edits that
   feature's spec/plan/tasks. It repairs spec drift on bugs in already-spec'd
   features; it is NOT a standalone ceremony for an arbitrary bug issue (which
   on a brownfield repo often has no owning feature spec — the locate
   heuristic would attach reports to unrelated features). MartyBonacci's
   standalone `specs/bugfix-NNN-*/` shape is the tier. The format-era gap
   costs little in practice: per D1/D11 the packed harness copies command
   markdown + scripts into the scaffold either way; `specify extension add`
   UX was never load-bearing. The `SPECKIT_NO_BRANCH` delta (D12) remains.
3. **Named follow-ups (filed with US3's PR, not built in it):**
   Quratulain's `bugfix` as an optional `after_implement` spec-consistency
   hook for FEATURE-tier work (right tool, that scope) + her `fix-findings` /
   `ci-guard`; upstream 0.16.4 hop (RunState TOCTOU fix — concurrent runs);
   `workflow run --json` + gate verdicts as settle telemetry; the #2319
   upgrade footgun (`specify init --here --force` silently skips existing
   scripts/templates — vendored-pin upgrades must delete-then-reinit).
