# Implementation Plan: Worker file memory — the repo carries the mind, the prompt carries the task

**Branch**: `feat/034-worker-file-memory` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/034-worker-file-memory/spec.md`

## Summary

Relocate the worker's repo-scoped memory from a host-side, append-only,
prompt-injected blob (`project_docs.repo_brief`, fed by the `REPO NOTES:`
hand-back line) into a committed `.devclaw/` directory in the project repo
(`MEMORY.md` index + `memory/*.md` one-fact files), maintained by the
worker under a write policy that lives as an instruction in the worker
skill bundle. The host's only remaining code on this path is protocol:
a one-line pointer in the dispatch brief when the index exists, a
mechanical seed of the empty layout on the onboard/migrate PR (boilerplate
revision 3), and an advisory doctor check. The hand-back lane — runner
parse, `PollResult.repo_notes`, the settle-side merge, the store, the
table — is deleted outright (hard cut, ruled at clarify), with its tests.
US3 collapses the duplicated speckit procedure in the host's advance brief
into a pointer at the skill that already carries it, so the generic
instruction text has exactly one home.

## Technical Context

**Language/Version**: Python 3.11 (host) + the stdlib-only runner; plain markdown skills

**Primary Dependencies**: none new

**Storage**: SQLite `devclaw.db` — one table REMOVED (`project_docs`, dropped at boot, idempotent); the repo's `.devclaw/` directory becomes the memory store, versioned with the code

**Testing**: pytest, fully stubbed; tripwire-class tests only (doctor seeded-fault, the legacy-shape instance check, one structural pointer guard, the skill-bundle cap + return-contract absence)

**Target Platform**: the deployed VPS instance (layers 2/4) + the sandbox image (`runner/skills/` is baked in; the deploy rebuilds the image on `runner/` changes)

**Project Type**: modular monolith; a single PR

**Performance Goals**: brief size independent of fact count (SC-001: ≥40 % shrink on the note-heaviest project, measured on the live instance after deploy); zero new model calls

**Constraints**: constitution III (no idle-tick work — everything happens at dispatch or in the session), IV (no new writer; a table removed, none added), II (one home for worker instructions), IX (Python only in the protocol domain; the write policy is an instruction)

**Scale/Scope**: ~15 host/runner files touched, net negative LOC on the host; one new skill file, one edited skill, one new doctor check

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. OAuth only** — no spawn site touched; no env change.
- [x] **II. Model-agnostic worker layer** — the memory protocol is plain markdown in `runner/skills/` (read half in `_common.md`, write half in `_writes-code/06-repo-memory.md`); the `.devclaw/` layout is plain markdown readable by any agent; no vendor wiring. The advance brief stops duplicating the speckit procedure the skill already carries (US3 = the one-home rule applied to the host's own brief text).
- [x] **III. Zero-token idle** — the only new host work is a file-existence probe at DISPATCH (below the work-present gate, same shape as the ARCHITECTURE.md pointer) and a doctor check run on demand. No tick-path read, no cognition.
- [x] **IV. Single writer** — the settle-side `write_repo_brief` writer is deleted; `project_docs` is dropped at boot (idempotent `DROP TABLE IF EXISTS`, precedent: the `programs` drop in `state_store/schema.py`). Memory writes happen in the sandbox and reach main through the increment's normal span + PR (FR-007) — git is the writer, reviewed like any change. Nothing reads a view back.
- [x] **V. Fail-closed verification / done is a proposal** — no gate touched. `.devclaw/` edits ride the materialize span like any file; no exemption added.
- [x] **VI. Loud failure** — the doctor memory check names every dangling index line / unindexed fact / oversize index (advisory WARN, spec FR-008). A missing skill bundle still fails loud (`skills_missing`); the seed on the onboard PR is reviewable, never a silent runtime write.
- [x] **VII. Fix the class** — the class is "push-based prose memory that is appended, never edited". The fix removes the append lane rather than tuning its dedupe or its cap (both rejected in the spec).
- [x] **VIII. Cognitive guardrail?** — none added; one removed (`merge_repo_notes`, the zero-LLM line-dedupe + eviction cap was a mechanism doing curation the agent should do by editing). No A/B seam needed: the lane is deleted, not disabled (a disabled lane is the #610 fork).
- [x] **IX. Instruct thin, verify thick** — Python added serves the **protocol** domain only: what goes into the worker (the pointer line, the seeded layout) and what doctor reads out (the advisory check). The memory policy itself is an instruction (`06-repo-memory.md`), checked by the scorecard's brief-size number, not enforced by code. No project-code knowledge enters devclaw; the standard practice adopted is "a committed docs directory, one fact per file" (the operator's own vault protocol), not a bespoke memory service.

**Post-design re-check (Phase 1)**: unchanged — the design added no writer, no tick work, no gate, no second instruction home.

## Project Structure

### Documentation (this feature)

```text
specs/034-worker-file-memory/
├── plan.md              # This file
├── research.md          # Phase 0: the seams as found in code, decisions
├── data-model.md        # Phase 1: the .devclaw/ layout + what is removed
├── quickstart.md        # Phase 1: how to validate (stubbed + live)
├── contracts/
│   └── memory-layout.md # the on-disk contract any agent can read/write
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
runner/
├── runner.py                          # REPO NOTES line out of the return contract; parser + payload field deleted
├── acp_client.py                      # one comment
└── skills/
    ├── _common.md                     # read half of the protocol (every kind incl. review)
    └── _writes-code/
        ├── 05-speckit-memory.md       # gains the one line the advance brief used to carry (plan/tasks missing ⇒ run the steps)
        └── 06-repo-memory.md          # NEW: the write policy (FR-003)

devclaw/
├── goal/
│   ├── repo_brief.py                  # merge/render/cap DELETED; worker_memory_pointer() ADDED next to architecture_map_pointer()
│   ├── tick_dispatch.py               # brief prefix = arch pointer + memory pointer; store read gone
│   ├── tick.py                        # _advance_brief: three instruction paragraphs → one pointer line (US3)
│   ├── tick_settle.py                 # repo-notes writeback block DELETED
│   ├── engine.py                      # _repo_notes() + PollResult wiring DELETED
│   ├── models.py                      # PollResult.repo_notes DELETED
│   ├── state.py                       # project_docs CREATE → DROP TABLE IF EXISTS (hard cut)
│   ├── state_content.py               # project_docs mixin methods DELETED
│   └── store/content.py               # read/write_repo_brief DELETED
├── speckit_setup.py                   # ensure_worker_memory_seeded() on the install + migrate PR paths
├── project_manifest.py                # BOILERPLATE_REVISION 2 → 3
├── engine/workspace.py                # one stale comment (.devclaw/trends.md → .devclaw/ memory)
└── doctor/
    ├── checks_project.py              # check_worker_memory (advisory, FR-008)
    └── checks_instance.py             # legacy dropped-shapes check also names project_docs

tests/
├── test_repo_brief.py                 # merge/cap tests REMOVED (ratchet); pointer guard ADDED (SC-004)
├── test_doctor.py                     # seeded-fault for the memory check; project_docs in the dropped-shapes test
├── test_runner_skills.py              # return contract: REPO NOTES absent; bundle cap lifted for genuine doctrine
├── test_runner_acp.py · test_acp_client.py · acp_fake_agent.py   # REPO NOTES fixtures/asserts REMOVED

docs/                                  # architecture.md, reference/env-vars.md, runbooks/doctor.md, INDEX.md; ARCHITECTURE.md; CLAUDE.md
```

**Structure Decision**: modular monolith, existing layers only. The pointer
lives beside the ARCHITECTURE.md pointer (layer 2 dispatch, pure fs probe);
the seed lives beside the `.goals/` gitignore seed (the onboard PR paths);
the check lives beside the goal-checkouts check (doctor project checks).
Every new line copies the nearest precedent's shape.

## Slicing decision

**One PR.** US1 + US2 are one coherent change (the relocation and the lane
retirement are two halves of the same cut; shipping US1 alone would leave
the blob still accumulating with nothing reading it). US3 is ~15 lines of
host text plus the seed and the doctor check, and stacking it would cost
the #235 retarget dance for no review benefit. Estimated non-test diff
≈ +250 / −220, well under the reviewability bound.

## Per-slice touch surface (the next session's read budget)

- **US1** — `devclaw/goal/repo_brief.py`, `devclaw/goal/tick_dispatch.py`, `runner/skills/_common.md`. Constraint: byte-identical brief for a repo without `.devclaw/` (SC-004).
- **US2** — `runner/runner.py`, `runner/skills/_writes-code/06-repo-memory.md`, `devclaw/goal/{tick_settle,engine,models,state,state_content}.py`, `devclaw/goal/store/content.py`, the four runner tests. Constraint: hard cut — no coexistence, no migration code; the table drop is idempotent at boot.
- **US3** — `devclaw/goal/tick.py::_advance_brief`, `runner/skills/_writes-code/05-speckit-memory.md`, `devclaw/speckit_setup.py`, `devclaw/project_manifest.py`, `devclaw/doctor/checks_project.py`, `tests/test_doctor.py`. Constraint: the `ADVANCE_BRIEF_MARKER` first line stays byte-identical (delivery + display detectors key on it); the seed runs only on the reviewable PR paths.

## Complexity Tracking

No constitution violations to justify.
