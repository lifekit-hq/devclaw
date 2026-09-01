# doctor-ready-contract-check — surface labeled issues the contract reader can't parse

## What

A doctor project check (`project.backlog.ready_contract`): every open
`devclaw-ready` issue on a project's repo must carry an acceptance section
`extract_acceptance` actually parses. Advisory (WARN), never a hold.

## Context

Found 2026-09-01: issues graded `devclaw-ready` on 08-18/19 (spec 009,
format-tolerant grading) carried no structured acceptance section — the
section only became load-bearing on 08-25 when spec 019 made the goal
contract read it live, and nothing re-validated the already-labeled
population. The drift then surfaced goal-by-goal as `MissingAcceptance`
dispatch blocks (#443 on 08-29) and false needs_human noise. The stubbed
suite structurally cannot see label-vs-contract drift on a live tracker —
the #641 class, which per spec 016 FR-014 gets a named doctor check with a
seeded-fault test.

## Requirements

- Reuse the wheels, invent nothing: `extract_acceptance` is the ONE contract
  reader; `READY_LABEL` has one home (`devclaw.intake`); repo parsing via
  `parse_owner_repo`; listing via the same `gh` road the contract pipeline
  uses.
- Advisory only — the dispatch boundary already fails loud; doctor surfaces
  the whole population at once. Remedy names `regrade_intake`.
- Unlistable backlog ⇒ UNKNOWN, never OK. No `repo_url` ⇒ OK short-circuit
  (also what keeps the stubbed suite subprocess-free — fixtures default
  `repo_url=None`).

## Plan

`check_backlog_ready_contract` in `devclaw/doctor/checks_project.py`, with
the gh listing behind a patchable module-global (`_list_ready_issues`, the
collector convention). PRs filtered out of the issues listing.

## Tasks

- [x] Check + registration in `PROJECT_CHECKS`.
- [x] Seeded-fault tests: WARN names only the unparseable issue; None ⇒
      UNKNOWN; no-repo_url short-circuit never touches the boundary.

## Done when

- `doctor` on a project whose repo has an open `devclaw-ready` issue without
  a parseable acceptance section reports `project.backlog.ready_contract`
  WARN naming the issue number(s) and the `regrade_intake` remedy.
- A backlog that cannot be listed reports UNKNOWN, never OK.
- The stubbed suite passes without any subprocess to `gh`.
