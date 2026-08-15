# Phase 1 Data Model — Speckit Execution Substrate (P1)

devclaw is brownfield; this feature adds no `devclaw.db` tables. The "entities"
are repo artifacts + one recorded field. Below: shape, source of truth, and the
one state machine that changes.

## Entities

### 1. Speckit setup (per repo)
- **Source of truth**: the repo's committed `.specify/` directory.
- **Fields (read-only, from files)**: `templates/`, `scripts/bash/`,
  `workflows/workflow-registry.json`, `memory/constitution.md`.
- **Derived**: `has_speckit = exists(.specify/)` — the adopt-vs-install signal (D5).
- **Not stored in devclaw.db.** `.specify/feature.json` is gitignored per-checkout
  state and is **not** authoritative for any devclaw decision (D2).

### 2. Feature artifact set (per feature)
- **Source of truth**: `specs/NNN-*/{spec.md, plan.md, tasks.md}` in the repo.
- Replaces the single repo `PLAN.md` blob.
- `tasks.md` is the parseable execution contract (see entity 3).

### 3. tasks.md checklist item
- **Format**: `- [ ] [ID] [P?] [Story] description` / `- [x] …` (speckit
  `[ID] [P?] [Story]` convention).
- **Fields the slice-guard reads**: only the checkbox state (`[ ]` vs `[x]`). The
  guard counts unchecked→checked flips between `HEAD^` and `HEAD`; it does **not**
  parse ID/priority/story for build-ahead detection (those are the worker's
  concern).
- **Contract**: `contracts/slice-guard-tasks.md`.

### 4. Executing-feature reference (per goal)
- **New recorded field**: the feature directory a goal is currently executing
  (e.g. `specs/012-foo`). Recorded at dispatch so the **done-gate** (D6) can ground
  on the right `spec.md`.
- **Storage**: on the existing goal status/contract (no new table); best-effort —
  absent ⇒ done-gate falls back to `done_when` text (transition-safe).

### 5. Install PR (per bare repo)
- The reviewable scaffolding change that adds `.specify/` to a repo with none.
- Ordinary devclaw delivery PR (`delivery/`), not a new type. State = its GitHub PR
  state; feature work for that repo is gated until merged (D5).

## State: adopt / install decision (onboard)

```
onboard(repo)
   │
   ├── has_speckit (.specify/ present, committed) ──► ADOPT
   │        └─ record adopt; write NO PLAN.md
   │
   └── no .specify/ ──► INSTALL
            └─ scaffold .specify/ → open REVIEWABLE PR (never silent commit)
                     │
                     ├── PR merged  ──► repo is speckit-ready → feature work allowed
                     └── PR open    ──► feature work BLOCKED (no half-installed state)
```

## State: slice-guard source selection (settle-time, D2/D4)

```
settle(increment)
   │
   ├── any specs/*/tasks.md present ──► sum [ ]→[x] flips across all tasks.md (HEAD^→HEAD)
   │                                     └─ > threshold ⇒ build-ahead → gate (fail-closed under strict)
   │
   └── none present ──► LEGACY fallback: read PLAN.md flips (removed by US4/shrink)
                          └─ neither present ⇒ 0 flips (fail-OPEN on detection, as today)
```

No other state machine changes in P1. Goal lifecycle
(`investigating→firming→executing`) is **untouched** in the MVP — its collapse is
the shrink slice (#539), not this feature.
