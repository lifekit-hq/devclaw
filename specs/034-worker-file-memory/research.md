# Research — spec 034 (2026-09-07)

Every unknown in the plan was settled by reading the code on `origin/main`
at `cdc1b88`. Facts newer than the spec are recorded first.

## Facts newer than the spec (verified in code)

- **The trend detector no longer exists.** Spec 037 (#844, 2026-09-06)
  deleted it with the summarizer, self-triage and the on-demand direction
  eval. The spec's edge case names it as a memory curator; the curators are
  the post-merge human review and the done-gate's grounded read. One stale
  comment survives in `devclaw/engine/workspace.py` ("the trend detector
  appending to a TRACKED .devclaw/trends.md") — fixed in passing because it
  is the only prior meaning of `.devclaw/` in the tree.
- **`runner/skills/` is the one home** (constitution II). #845 added
  `_writes-code/45-self-review.md`; the writes-code bundle is 13 157 chars
  against a 13 200 cap in `tests/test_runner_skills.py`. The memory protocol
  is genuine doctrine (spec 034 FR-003), so the cap is lifted with a
  docstring note, as every prior doctrine addition did — after compressing
  what the advance brief stops repeating.
- **Boilerplate revision 2** (2026-09-06) is the `.goals/` gitignore line,
  seeded by `speckit_setup.ensure_goal_checkouts_ignored` on both the
  install and the migrate PR paths, reported by
  `doctor.check_goal_checkouts_ignored`. Revision 3 = the `.devclaw/`
  seed, same three seams.

## The lane as found (what US2 deletes)

| Hop | Code | Fate |
|---|---|---|
| Worker instruction | `runner/runner.py::_RETURN_CONTRACT` "REPO NOTES:" line | line removed |
| Parse | `runner/runner.py::_REPO_NOTES_LINE_RE`, `_parse_repo_notes`, `result_payload["repo_notes"]`, `blocked_payload["repo_notes"]` | deleted |
| Poll | `devclaw/goal/engine.py::_repo_notes`, `PollResult.repo_notes` (`goal/models.py`) | deleted |
| Merge | `devclaw/goal/repo_brief.py::merge_repo_notes`, `MAX_BRIEF_CHARS`, `scope_key_for` | deleted |
| Persist | `GoalStore.content.write_repo_brief/read_repo_brief` → `GoalStateContentMixin.write_project_doc/read_project_doc` → table `project_docs` (`goal/state.py`) | methods deleted; table dropped at boot |
| Inject | `tick_dispatch.py` `notes_prefix = render_brief_prefix(store.read_repo_brief(scope))` | replaced by `worker_memory_pointer(checkout)` |
| Settle | `tick_settle.py` "Repo-scoped worker brief writeback" block | deleted |
| Tests | `tests/test_repo_brief.py` US2 section; `acp_fake_agent.py` REPO NOTES lines; `test_runner_acp.py::test_ok_run_emits_contract_result_and_events` repo_notes assert; `test_acp_client.py` "REPO NOTES:" assert | removed / re-pointed (symmetric ratchet) |

`project_docs` held exactly one kind (`repo_brief`); no other reader or
writer exists (`grep -rn project_docs devclaw` → state.py DDL + the mixin).

## Decisions

- **Decision: drop `project_docs` at boot with `DROP TABLE IF EXISTS`.**
  Rationale: the clarified hard cut ("deploy … drops the blobs"); the
  precedent is `DROP TABLE IF EXISTS programs` in
  `devclaw/state_store/schema.py` (spec 022 demolition). Idempotent, no
  one-shot module to delete later. The doctor legacy-shapes instance check
  gains `project_docs` so a DB that somehow kept it is reported (the
  convention: a state-shape change ships its doctor check).
  Alternatives: leave the table in place unused — rejected, it is the
  second memory home the one-home rule forbids and would look live to the
  next reader.
- **Decision: the pointer probes `<checkout>/.devclaw/MEMORY.md`**, the
  goal checkout the worker actually runs in (same argument
  `architecture_map_pointer` takes), not `goal.workspace_dir`. Rationale:
  since "one goal, one checkout" the project checkout is the seed mirror;
  the worker's tree is the goal checkout, and the pointer must describe
  what the worker will find.
- **Decision: the read half of the protocol goes in `_common.md`** (every
  kind, including `review_repository`), the write half in
  `_writes-code/06-repo-memory.md` (code-writing kinds only). Rationale: a
  reviewer benefits from recorded gotchas exactly as a worker does; only a
  kind that commits may write. The old lane skipped review kinds for the
  opposite reason (prior claims seeding a grounded read) — a committed,
  reviewed file is not an unverified prior claim, it is part of the repo
  under review.
- **Decision: seed only `MEMORY.md`** (index + the policy stated in-file);
  `.devclaw/memory/` is created by the first fact. Rationale: git does not
  track empty directories, and a `.gitkeep` is boilerplate for its own
  sake. The index's in-file policy is what the spec's "empty layout with
  the write policy stated in-file" asks for.
- **Decision: the advance brief keeps its first line byte-identical** and
  replaces the three procedure paragraphs with one line pointing at the
  skill. Rationale: `ADVANCE_BRIEF_MARKER` is the detector delivery and the
  display choke point key on (#547/#550); the procedure already lives in
  `05-speckit-memory.md`, so the paragraphs were the second home. The one
  clause the brief carried that the skill did not — a feature missing
  plan/tasks gets the speckit steps before implementation — moves INTO the
  skill (one line).
- **Decision: doctor memory check is a single `project.worker_memory.health`
  finding**, WARN on any of: an index line whose target file is missing, a
  fact file not in the index, more than 30 index entries. Absent `.devclaw/`
  ⇒ OK "not seeded" (absence is a supported state; the revision check
  already says whether the repo is behind). No hard cap anywhere.
- **Decision: skill-bundle cap 13 200 → 13 600.** Rationale: the memory
  write policy is doctrine the spec mandates, and the existing test's own
  history lifts the ceiling for exactly that class; the read half sits in
  `_common.md`, which every kind loads, and is kept to four sentences.

## Rejected during planning

- A `memory` MCP tool or host-side read of fact files at dispatch (to
  inline "relevant" facts): re-creates push memory; the spec rejects a
  host-side memory service by name.
- Having the onboard AGENT author the seed: the seed is mechanical
  boilerplate (like `.goals/`), and the onboard skill is read-only by
  contract except for its three docs + Dockerfile + workflow; a mechanical
  seed on the same PR keeps the skill unchanged.
- Keeping `merge_repo_notes` as a curation helper for the worker: the
  worker edits files; a host-side dedupe over prose is the mechanism the
  spec names as the rot source.
