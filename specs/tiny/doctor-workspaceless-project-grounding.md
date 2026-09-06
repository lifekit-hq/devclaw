# TinySpec: doctor project checks never fall back to devclaw's own checkout

**Branch**: goal/issue-819-nested-npmrc-2026-09-05
**Date**: 2026-09-06
**Status**: done
**Complexity**: small

## What

Four of doctor's project checks resolve a project's workspace as
`Path(project.workspace_dir or "")`. When the row carries no `workspace_dir`,
that expression is `Path(".")` — **the devclaw host process's own checkout** —
and the checks read it as if it were the project. devclaw's repo happens to
carry an `AGENTS.md`, a `.specify/`, a `.git` and a `devclaw.json`, so a
workspace-less project collects four confident **OK** verdicts that describe
devclaw itself. Observed PRE-FIX, doctor run against a project row carrying no
`workspace_dir` (the evidence strings below are the old ones):

```
project.workspace.preflight       FAIL     no workspace_dir to dispatch into
project.manifest.presence         UNKNOWN  workspace not on disk — manifest state unknowable
project.markers.integrity         OK       devclaw:managed markers well-formed
project.scaffold.drift            OK       .specify/ matches the packaged scaffold
project.scaffold.tracked_state    OK       .specify/feature.json is not tracked (scaffold-gitignored)
project.capabilities.undeclared   OK       no undeclared registry dependency visible
```

A row with no `workspace_dir` is legal, not corrupt: `_validate_workspace_path`
accepts `None` on purpose because "a project may be registered before its clone
exists". So this is a reachable steady state, not a broken-invariant edge.

## Context

Found self-reviewing the #819 fix (`specs/tiny/undeclared-registry-nested-npmrc.md`),
which widened `check_capability_declaration` from two head reads to iterating
the root's immediate subdirectories. On a workspace-less project that change
enlarged the misread from 2 files to up to ~130 — all under devclaw's own tree.
The underlying fallback predates #819 and affects three further checks, so the
fix belongs at the class, not at the one function #819 touched (CLAUDE.md,
"fix the class, not the instance").

Two standing rules make these false OKs a defect and not a cosmetic one:

- **Loud failure over silent degradation.** An OK is doctor's affirmative
  health claim. Claiming health for a project whose workspace is not on disk is
  the exact silent degradation doctor exists to catch, and it is emitted with
  `project_id=` attribution so an operator cannot tell it apart from a real
  verdict.
- **Grounding (the #227 shape).** Repo reasoning may never be inferred "from
  your own working directory, the host process context, or any repository you
  have seen before; absent ⇒ unknown". The rule is written for cognition
  prompts; a mechanical check that silently substitutes the host checkout
  breaks it more cheaply and more quietly.

`check_manifest` already gets this right (`if not ws or not Path(ws).exists()`
⇒ UNKNOWN). It is the in-repo precedent; the fix is to give the other four the
same guard from ONE shared home rather than a fifth hand-copy.

| File | Role |
|------|------|
| `devclaw/doctor/checks_project.py` | Modified — new `_grounded_workspace` guard; the five workspace-reading checks route through it |
| `tests/test_doctor.py` | Modified — `test_workspaceless_project_is_never_judged_from_the_host_checkout`, named after the invariant and parametrized over the two shapes (absent / never-cloned), beside the undispatchable-workspace case it extends the class of |

## Requirements

1. A project whose `workspace_dir` is absent, blank, or not a directory on disk
   yields `UNKNOWN` from every check that reads the workspace — never `OK`,
   never a verdict derived from the devclaw process's own tree.
2. The guard lives in ONE helper; no check re-derives the workspace path.
   `check_manifest`'s existing correct guard is replaced by the helper rather
   than left as a fifth divergent copy.
3. `project.workspace.preflight` keeps owning the loud verdict — it already
   FAILs with the actionable reason and remedy. The other checks say only that
   they cannot judge; the guard must not turn one missing workspace into five
   FAILs shouting the same fact.
4. A project WITH a real workspace is unaffected — every existing verdict,
   evidence string and remedy is byte-identical.
5. Mechanical and bounded: one `is_dir()` stat per check, no network, no
   cognition, no writes.

## Plan

1. `_grounded_workspace(project, cid) -> tuple[Path | None, list[Finding]]` —
   returns `(path, [])` when the workspace is a real directory, else
   `(None, [UNKNOWN finding])`. Copies `check_manifest`'s guard shape and
   carries `check_workspace_preflight`'s `update_project (or restore the
   workspace checkout)` remedy (`check_manifest`'s UNKNOWN had none).
2. Route `check_manifest`, `check_marker_integrity`, `check_scaffold_drift`,
   `check_tracked_checkout_state` and `check_capability_declaration` through it.
3. A test named after the invariant, asserting each of the five checks reports
   UNKNOWN. Deliberately NOT "no finding is OK": the registry- and
   goal-store-derived checks (`links.dangling`, `links.unstamped_goals`,
   `goals.issue_refs`, `backlog.ready_contract`) read no workspace and are
   legitimately OK for such a project — a blanket assertion would demand they
   lie.

## Rejected alternatives

- **Guard only `check_capability_declaration`** (the function #819 touched).
  Leaves three identical false-OK checks beside the fixed one — the
  "fix that only unwedges the case that hurt today" CLAUDE.md names as a smell.
- **Make the missing workspace a FAIL in every check.** Duplicates
  `project.workspace.preflight`'s verdict five times over; one root cause
  should produce one loud finding plus four honest "cannot judge"s. UNKNOWN is
  the verdict doctor already reserves for unjudgeable state.
- **Skip the checks entirely (emit nothing).** Silence is indistinguishable
  from a check that never ran, and doctor's contract is that a check reports
  even when it crashes. UNKNOWN is visible; omission is not.
- **Strip the stored path before testing it.** Caught in review: the registry
  validates a stripped value but stores the ORIGINAL
  (`_validate_workspace_path` vs `create`), so `" /workspace "` is a reachable
  row. Stripping made the helper accept a path `workspace_is_dispatchable`
  rejects — preflight FAILing while the other five happily judged the
  directory. The helper now tests blankness on the stripped value but uses the
  path RAW, exactly as `workspace_is_dispatchable` does, so the two can never
  disagree about what is on disk.
- **Fix it in `Project.workspace_dir` (default to a sentinel / make it
  non-optional).** The registry deliberately allows a project to be registered
  before its clone exists; changing that is a spec-003 admission change, far
  outside a doctor read-path bug.

## Tasks

- [x] `_grounded_workspace` helper in `checks_project.py`
- [x] Route the five workspace-reading checks through it
- [x] A doctor test named after the invariant, over both workspace-less shapes
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] A registered project with no `workspace_dir` reports UNKNOWN (not OK)
      from `markers.integrity`, `scaffold.drift`, `scaffold.tracked_state`,
      `manifest.presence` and `capabilities.undeclared`
- [x] `project.workspace.preflight` still carries the one loud FAIL
- [x] A project with a real workspace keeps every existing verdict unchanged
- [x] Full suite + `ruff check .` + `mypy` green
