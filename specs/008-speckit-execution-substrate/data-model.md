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

---

# US3 additions (P2 increment, 2026-08-18)

### 6. Ceremony tier (routing table — pure data, no storage)

| Signal (label or `dispatch_task` kind) | Tier | Workflow the brief stamps | Artifacts |
|---|---|---|---|
| `feature` / `enhancement` / kind=`implement_feature` | full | core specify→plan→tasks→implement | full `specs/NNN-*/` set |
| `bug` / kind=`fix_bug` | bugfix | vendored `speckit.bugfix` (regression-test-first) | lightweight `specs/bugfix-NNN-*/` set |
| `critical-fix` / `hotfix` | hotfix | vendored `speckit.hotfix` (expedited + post-mortem) | hotfix incident set |
| `chore` / `docs` | direct | none — direct-advance | **none** |
| ambiguous / absent / conflicting | full (or needs-human) | careful path | — |

- **Resolution is mechanical** (dict lookup at dispatch, zero LLM — D10); the
  chosen tier is stamped into the brief; the worker never re-decides it.
- **Monotone rule**: uncertainty routes only UP the ladder (toward full), never down.
- Multiple labels: the **highest-ceremony** matching label wins (feature > bugfix
  ordering-wise; hotfix beats bug; a `feature`+`bug` pair is conflicting ⇒ full).

### 7. Vendored workflow pack (per packed harness, not per repo run)

- **Source of truth**: pinned copy of MartyBonacci/spec-kit-extensions'
  `bugfix` + `hotfix` workflows inside devclaw's packed speckit scaffold
  (`.specify/extensions/workflows/{bugfix,hotfix}/` + the two command markdowns
  as worker-skill content + `scripts/create-{bugfix,hotfix}.sh`).
- **Pin**: upstream commit SHA recorded in a vendor README in the pack dir;
  the ONLY local delta is the `SPECKIT_NO_BRANCH=1` branch-creation guard (D12).
- **Registration**: speckit's own mechanism (`workflow add` from local path /
  registry entry in the scaffold) — FR-009; no devclaw abstraction.
- `specs/bugfix-*/tasks.md` is read by the **unchanged** slice-guard glob (D2).

## State: tier routing (dispatch-time, D10)

```
dispatch(work item)
   │
   ├── kind=fix_bug ─────────────────────────────► BUGFIX tier
   ├── kind=implement_feature, no issue labels ──► FULL cycle
   └── labeled issue ──► highest-ceremony label match:
            ├── critical-fix|hotfix ──► HOTFIX tier
            ├── feature|enhancement ──► FULL cycle
            ├── bug ─────────────────► BUGFIX tier
            ├── chore|docs ──────────► DIRECT advance (no artifact)
            └── none/conflicting ────► FULL cycle (careful path; never silently lighter)
```
