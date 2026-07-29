# Proposal — the foundation checkpoint: verify load-bearing `done_when` constraints early, not at the final gate

- **Status:** **DRAFT** — 2026-07-29. Filed from the v0.1-proof triage (issue #432).
  Four `[OPEN]`s in §5 must be resolved (or explicitly deferred-with-owner) before
  this can flip to LOCKED. **No code before lock** (spec-lifecycle).
- **Date opened:** 2026-07-29 · **Authors:** Denys + Claude
- **Grounded on:** the `finance-sentry-ui-library` incident (problems catalog +
  live goal state, 2026-07-29) and a code-grounded design pass over
  `goal/phases/firming.py`, `firmed.py`, `tick.py`, `tick_settle.py`, `models.py`
  @ `origin/main` `48212fa`.
- **Relates to / does not restate:** the invariants in
  [`CLAUDE.md`](../../CLAUDE.md) — zero-token idle guard, verification fails
  closed, single-writer via `GoalStore.transition`. This proposal introduces a
  **new early gate**; it does not touch the done-gate's strictness. It reuses the
  existing `ItemAssert` machinery (`models.py`, enforced in `tick_settle`), whose
  docstring already names the ng-zorro fake-install exhibit as the class it exists
  to close — this lifts that per-item check to the **goal level**.

## The problem (issue #432)

An explicit, **load-bearing** `done_when` constraint — a required base
dependency/framework, a required package name — is only re-checked at the
**final done-gate** (`tick_donegate`), after weeks of sunk cost.

`finance-sentry-ui-library` required the component library be built on
**`ng-zorro-antd`** as the base primitive layer. The executor built **42
components on Tailwind+CDK** — the wrong foundation — and nothing caught it until
the done-gate decomposed `done_when` into clauses. By then the only exits were a
large, high-risk 42-component rewrite or abandoning the requirement (abandon was
chosen). The firming/discovery step even **identified** this decision as *"the
single highest-cost, highest-risk piece of this whole outcome"* — yet execution
proceeded on the wrong premise anyway.

**The class:** a constraint that is cheap to verify the moment the foundation
exists (does the repo depend on the required framework? does the package name
match?) is instead discovered only at done-evaluation. Enforce it early.

## Why this isn't already solved

`ItemAssert` (`devclaw/goal/models.py`) is a mechanical, fail-closed,
workspace-bounded `file_exists`/`grep` predicate, enforced when a checklist item
settles (`tick_settle._check_addressed_asserts`). The gap: it is **per-item and
checked only when that one item settles**. A load-bearing constraint not pinned
to an early item — or a foundation torn out after an early item passed — is
invisible until the done-gate. The fix is to lift that same un-fakeable check to
the **goal level**, derived once by firming, enforced once early.

## The design (systemic, domain-general — NOT an ng-zorro special-case)

Two halves, matching the mechanism/cognition split the invariants demand:

1. **Derive (cognition — already runs).** A pure mechanical probe can't know
   *which* constraint is load-bearing; that's semantic, buried in `done_when`
   prose. **Firming** is the one-shot step whose whole job is turning prose
   `done_when` into structured, verifiable criteria, and it already carries the
   grounded `REPOSITORY CONTEXT` block + the no-inference clause. Firming emits a
   small list of **`foundation_asserts`** — `ItemAssert`-shaped predicates
   (`{kind, path, pattern, absent}`, reusing the existing parse-time security
   guards) — one per success criterion that (a) names a concrete required base
   dependency/package/framework AND (b) is checkable the moment the foundation
   exists. Grounded strictly in `REPOSITORY CONTEXT`/discovery brief, never
   priors. **Empty list is the common case** (most goals have no such
   constraint) → the checkpoint is a byte-identical no-op.

2. **Check (mechanical — zero LLM).** After the **first substantive delivery**,
   gated *after* the zero-token `should_plan` gate in `_handle_executing`, run
   the existing `_check_item_asserts_sync` against the workspace. On failure →
   `GoalStore.transition(Event.BLOCK)` with `blocked_kind="needs_answer"` (the
   foundation being wrong will NOT self-heal — the owner must decide: re-lay it,
   accept the drift, or re-scope). On pass → set a fire-once flag so it never
   re-runs.

**No new tick-path LLM call** — the derivation is firming, which already runs;
the check is file-IO. The zero-token idle guard and the `FakeClaude.calls == 0`
idle assertions stay green.

## Sizing — slice, firm P1 only

- **P1 (firm + size this):** the goal-level foundation checkpoint over
  firming-derived `foundation_asserts`, fired once after the first delivery,
  zero-LLM, fail-closed, `needs_answer` block — **plus** the firming
  prompt/schema change to emit `foundation_asserts`. P1 alone is a viable result:
  it catches the #432 class the moment the wrong base first ships. Est. ~1 PR;
  the one migration-touching surface is the fire-once flag (see `[OPEN-1]`).
- **P2 (named, unsized):** re-check already-`done` items' asserts on later
  deliveries — catch a foundation *torn out* mid-flight, not just never
  established.
- **P3 (named, unsized):** decomposer-side — pin the derived `foundation_asserts`
  onto the first scaffold checklist item so the per-item breaker catches it even
  earlier.

## §5 — `[OPEN]` items (mandatory clarify before LOCK)

- **`[OPEN-1]` Fire-once flag home.** New `GoalStatus.foundation_checked: bool`
  (clean + legible, but ripples into the status-serialization/migration surface —
  the same 8-site column dance #430 just did) **vs** a `goal_docs` marker (no
  migration, but a second read). *Recommendation: the bool, for legibility.*
- **`[OPEN-2]` Check hook site.** `_handle_executing` right after `should_plan`
  (keeps it beside the other executing-phase gates, reuses the zero-token guard)
  **vs** inside `tick_settle._resolve_polling_action` (the delivered tree is
  already on disk there). *Recommendation: `_handle_executing`.*
- **`[OPEN-3]` Fire timing.** "After the first substantive delivery" — confirm
  the exact predicate (first delivery recorded? first checklist item done?) and
  that it **defers** (does not block) before any delivery exists, so an empty
  fresh repo never false-fails.
- **`[OPEN-4]` Firming reliability + cost.** Does asking firming for
  `foundation_asserts` meaningfully bloat the firming prompt or its failure rate?
  (Firming is Opus-tier already.) Confirm the schema addition is grounded and
  that a firming that emits *no* asserts degrades safely to today's behavior.

## The hard rule (spec-lifecycle)

This is a **new mechanism** spanning three layers (a firming cognition output +
prompt, a new `GoalStatus` field + migration, a new tick-phase gate + block
semantics) — exactly the multi-PR direction change the spec-lifecycle rule
governs. **No code until this is LOCKED** with the four `[OPEN]`s resolved.
