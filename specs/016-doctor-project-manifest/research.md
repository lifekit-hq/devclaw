# Research: Instance Doctor + Per-Project Manifest (spec 016)

Phase 0 output. Every decision grounded in a code read (2026-08-24 exploration
of the repo at `main`). Format: Decision / Rationale / Alternatives considered.

## R1. Where the doctor check engine lives

**Decision**: New read-only package `devclaw/doctor/` (`model.py`,
`checks_instance.py`, `checks_project.py`, facade in `__init__.py`), with
stores injected as arguments. Layer-1 surfaces stay thin: the MCP tool module
binds the `_state` singletons; the CLI subcommand constructs stores the way
`cli.py` already does.

**Rationale**: Tools register by import side effect and rebind state at module
level (`server/tools/__init__.py:11-25`), so check logic in the tools layer
would be untestable without the server. Injection matches how `quality/` and
`project_registry.project_rollup` are consumed.

**Alternatives**: a single `devclaw/doctor.py` module (rejected — three check
domains plus report model crowd one file); putting checks in `loom/`
(rejected — the extraction-boundary AST guard forbids loom importing devclaw
internals, and doctor must read GoalStore/registry).

## R2. What "is this DB current?" means mechanically

**Decision**: The primary instance check asserts the four one-shot migration
meta keys exist (`goal_view_migration_done_at_ms`,
`goal_legacy_cutoff_done_at_ms`, `goal_project_id_backfill_done_at_ms`,
`trace_response_text_migration_done_at_ms`) via `store.get_meta`, plus direct
SELECT probes for the structural legacy shapes the cutoffs were meant to
erase: `goal_status.lifecycle` NULL/≠`executing`, `goal_deliveries.ref_id`
NULL, table `goal_docs` still present, column
`goal_status.inbox_ingest_cursor` still present.

**Rationale**: There is no `schema_version` column or `user_version` pragma
anywhere — the meta keys ARE the instance's version markers. The SELECT
probes catch the #641 class (rows in shapes the code no longer writes) that
the tolerant readers deliberately hide (`load_goal` ignores unknown keys;
`strictness`/`mode` coerce silently).

**Alternatives**: introducing a real `PRAGMA user_version` scheme (rejected
for this spec — a migration-framework change is its own arc; the meta keys
already carry the information); checking only meta keys without shape SELECTs
(rejected — a meta key proves the sweep ran once, not that no legacy row
crept back in).

## R3. Credential check without invoking claude

**Decision**: Four mechanical probes: (a) `.credentials.json` presence under
the *container-local* claude dir + parse of its OAuth `expiresAt` epoch-ms
(warn when past or < 48 h out); (b) `.claude.json` present, parseable,
non-empty `oauthAccount`; (c) `CLAUDE_CODE_OAUTH_TOKEN` set/blank (presence
reported, value never echoed); (d) current `pause_reason` meta indicating an
active auth pause. All file paths via `config.host_claude_dir()` /
`claude_trust.config_path_for()`.

**Rationale**: Nothing in the repo reads `expiresAt` today — the only auth
"detection" is post-hoc regex classification of failures
(`loom/limits.py:128-134`), which is exactly why auth death is discovered at
2 a.m. The check is new but the file locations and parsing precedent
(`claude_trust.py`) exist. `sandcastle.py:131-133` warns the host claude dir
may be un-stat-able when devclaw runs containerized → that case reports
`unknown`, not `ok`.

**Alternatives**: live `claude --print` probe (rejected — violates the
zero-LLM guarantee and burns quota); reusing only the pause state (rejected —
that detects death after the fact, doctor's job is before).

## R4. Skills-bundle check

**Decision**: Resolve the bundle for all four known kinds using the runner's
own pure resolver — `from runner.runner import _skill_paths_for_root`
(import-guarded; unimportable ⇒ the check reports `unknown` with the import
error) — against the same root the host engine injects
(`engine/host.SKILLS_DIR`).

**Rationale**: `_skill_paths_for_root` (runner.py:80) is stdlib-pure and IS
the canonical resolution; duplicating its glob logic in doctor would be the
#610 silent-fork class this spec exists to detect. The runner check itself
(`skills_missing`, runner.py:222-229/1043) fires only after a container
launch — doctor closes the "no host-side pre-dispatch check" gap.

**Alternatives**: reimplementing a minimal presence check in doctor
(rejected — logic fork); moving the resolver into a shared module (rejected
for this arc — `runner/` is deliberately standalone, spec 011; an
import-guarded read of a pure function does not add a runner dependency on
devclaw, only the reverse, which tests already do).

## R5. Run-window drift detection

**Decision**: Read the RAW meta key (`store.get_meta("run_schedule")` and the
per-goal `run_schedule:<goal_id>` keys) to distinguish three states: absent
(never set / lost on redeploy), present-but-corrupt (json parse fails), and
present-valid; then layer the existing `get_run_schedule`/`operator_block`
verdicts on top for the "is dispatch open right now" evidence line.

**Rationale**: `control.py:188-196` makes `get_run_schedule` return
`DEFAULT_SCHEDULE` (enabled: False) when the key is absent *or corrupt* —
the exact silent degradation that made "run window resets on redeploy" a
recurring memory-note item. Only the raw read can tell "operator disabled"
from "row gone".

**Alternatives**: adding a "was ever set" flag to the store (rejected — the
raw read answers it without a new writer).

## R6. Stale project→goal links

**Decision**: Three link checks per project: (a) `goal_ids` entries that
resolve to no existing goal (dangling advisory links — today they produce NO
finding because nothing sets the vestigial `"missing"` marker); (b) goals
whose `workspace_dir` maps onto the project but whose `project_id` is unset
(the one-shot backfill at `goal/project_id_cutoff.py` never re-runs); (c)
`workspace_is_dispatchable(project.workspace_dir)` reused verbatim from
`engine/workspace.py:24`.

**Rationale**: `project_rollup` joins on `project_id` only and silently emits
nothing for dangling links; the `[MISSING — dangling link]` render path in
the CLI is dead code because no producer sets the flag. Doctor becomes that
producer's replacement — as a finding, not a row mutation.

**Alternatives**: making doctor auto-heal links via `unlink_goal` (rejected —
doctor never mutates; `link_goal`/`unlink_goal` remain the remedy verbs).

## R7. Manifest doorway shape

**Decision**: `devclaw/project_manifest.py` — module constants
`MANIFEST_NAME = "devclaw.json"`, `SCHEMA_VERSION = 1`,
`BOILERPLATE_REVISION = 1`; a frozen `Manifest` dataclass; `parse_manifest
(text) -> Manifest` raising typed `ManifestError` on any malformation
(unknown schema_version > supported ⇒ the distinct "instance too old" error);
`load_manifest(workspace_dir, ref=None) -> Manifest | None` — `None` when the
file is absent at the ref, loud `ManifestError` when present-but-malformed;
`ref=None` reads the worktree file, `ref="<sha>"` reads via
`git -C <ws> show <sha>:devclaw.json` (the `slice_guard.py:155` mechanism,
but with `task_change.py`'s not-best-effort error posture, not slice_guard's
fail-open one).

**Rationale**: Mirrors the `config.py` doorway contract stated at its
docstring (one home, one default, one parse) on the per-repo axis; the
`_raw()` convention maps to returning the typed record and letting the ONE
error policy live in the doorway. No new `DEVCLAW_*` env var is needed, so
the single-doorway AST guard is untouched.

**Alternatives**: YAML manifest (rejected — Denys specified JSON; JSON also
gets `$schema` editor validation for free); reading via `resolve_override`
only (rejected — `resolve_override` covers project-row knobs; the manifest is
a different source tier and the doorway composes with it rather than hiding
inside it).

## R8. Strictness "most-specific-wins, resolved live"

**Decision**: Persist `strictness` in `goal.yaml` only when explicitly set
(create param or `set_strictness`); the loader returns the raw value or
`None` (today it coerces absent → "trust", erasing explicitness). A pure
resolver `effective_strictness(goal_raw, manifest_default) -> Strictness`
(unrecognized ⇒ "strict"-side fail-closed per `gate_policy`'s posture) is
called at the goal-level read sites: the dispatch snapshot
(`goal/engine.py:86/107/160` feed → row snapshot), the done-gate
(`evaluator.py:665`), and the slice guardrail (`tick_settle.py:372`).
Task-gate reads keep using the row snapshot taken at dispatch — the snapshot
happens pre-run, so it is equivalent to a `pre_run_sha` read and the worker
cannot influence it.

**Rationale**: Implements the clarified precedence (explicit goal > manifest
> instance default, live) with the minimal shape change: one nullable read,
one pure resolver, no new table. "Live" means every fresh goal-level
decision; already-dispatched tasks keep their snapshot (changing a gate
regime mid-run would make gate outcomes racy).

**Alternatives**: `strictness_source` companion field (rejected — the
nullable raw value carries the same bit); resolving inside `gate_policy.
gate_consequence` (rejected — that function stays pure policy; source
resolution is a different concern).

## R9. verify_cmd precedence

**Decision**: Extend the existing resolution expression at the three
`goal/engine.py` sites to `action.verify_cmd or goal.verify_cmd or
manifest.verify_cmd` (manifest read from the worktree at dispatch time —
pre-run, so tamper-safe). `change_advisories` keeps reading the row snapshot.

**Rationale**: Most-specific-wins, consistent with strictness; the dispatch
site is pre-run so no `git show` needed there. The #233 lesson holds: the
planner action tier already existed and is host-validated; the manifest tier
is human-authored, PR-reviewed input.

**Alternatives**: manifest overriding goal (rejected — a goal author who
explicitly set verify_cmd said something more specific than the repo
default).

## R10. Browser-gate surface kind

**Decision**: Manifest field `surface: "app" | "library"`. At the two settle
seams (`quality/task_gates.py:128`, `queue/settle.py:1257`) read the manifest
**at `pre_run_sha`** via the doorway; `surface: "library"` short-circuits the
gate as the existing library exemption (not_triggered/library-exempt path);
`"app"` (or absent manifest) keeps today's glob heuristics
(`DEFAULT_FRONTEND_GLOBS` / `DEFAULT_LIBRARY_GLOBS`) unchanged.

**Rationale**: `browser_run_verdict` already threads `globs`/`library_globs`
kwargs that no caller supplies — the seam exists. Declaring the surface kills
the finance-sentry-ui-library wedge class at its root (inference → declaration).
This is the one manifest consumer that reads post-run, hence the pre_run_sha
pin (FR-009's named regression test lives here).

**Alternatives**: per-project glob lists in the manifest (rejected for v1 —
schema surface area without a driving incident; the two-value enum covers the
known failure class and the schema can grow in rev 2).

## R11. Malformed-manifest dispatch rejection

**Decision**: `_preflight_or_prep` (server/tools/_common.py:44) and the goal
dispatch path call the doorway's worktree read; `ManifestError` maps to a
loud dispatch rejection with the parse error and file path in the message.
Absent manifest ⇒ proceed on instance defaults (plus a doctor warn).

**Rationale**: FR-010 verbatim; the preflight seam is where unknown-project
and undispatchable-workspace already reject loudly, so the class lands in its
existing home.

## R12. Manifest seeding + boilerplate revision + US3 drift scope

**Decision**: `speckit_setup.install_speckit_pr` seeds `devclaw.json` (schema
1, current `BOILERPLATE_REVISION`, `surface` defaulted from the existing
marker heuristics at seed time, no verify_cmd) when the repo lacks one — same
reviewable PR, never a silent commit. The onboard skill is amended to name
`devclaw.json` human-owned (the agent must not author or edit it). Doctor's
drift checks are scoped to what is mechanically diffable: (a)
`boilerplate_revision` vs the code constant; (b) `devclaw:managed` marker
integrity (start/end pairing, duplication) in AGENTS.md; (c) `.specify/`
scaffold drift against the packaged source (`_resolve_speckit_source`), which
IS file-copied and therefore diffable. **Content drift of the LLM-authored
prose docs (AGENTS/README/ARCHITECTURE) is explicitly out of scope** — no
canonical template exists to diff against; the spec's US3 wording is amended
in this arc to match (spec change: "managed-block drift vs canonical
templates" → "marker integrity + scaffold drift + revision currency").

**Rationale**: The exploration confirmed the boilerplate is LLM-authored from
a skill, not templated — a content diff would need cognition, which doctor
forbids. Marker integrity + revision + scaffold diff cover the #610 fork
class mechanically.

**Alternatives**: hashing managed-block content into the manifest at onboard
time and diffing hashes (deferred to a future schema rev — workable but adds
a devclaw-written field into flow near the human-owned file; needs its own
design pass to respect the no-second-writer rule).

## R13. Report determinism

**Decision**: Findings carry no timestamps; ordering is fixed (instance
section in declared check order, projects sorted by id); evidence strings are
derived from state only. Two runs over unchanged state produce byte-identical
reports.

**Rationale**: Spec edge case; also makes the seeded-fault tests trivial
golden assertions.
