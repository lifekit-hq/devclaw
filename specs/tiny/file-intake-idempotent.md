# TinySpec: `file_intake` — file a development problem once, count the repeats

**Branch**: fm/iss-devclaw-1023
**Date**: 2026-11-16
**Status**: in review
**Complexity**: small (one tool module, one test file, one README line)
**Issue**: #1023 — file_intake is idempotent by repo plus failure fingerprint

## North-star case

- **Failure moved**: *ran but needed the owner*. Agents that hit the same
  development-class failure each night file it through devclaw over MCP; without
  idempotence the owner triages N duplicates of one problem. With it the owner
  sees one issue whose count climbs.
- **Number that shows it**: duplicate open issues per (repo, fingerprint):
  today unbounded for any caller; after, zero
  (`tests/test_file_intake_idempotent.py`, including a concurrent double call).
- **Cut when**: callers pass free-text fingerprints that never collide (the
  derived fingerprint is too fine) or collide across unrelated failures (too
  coarse).

## What

- A NEW narrow MCP tool `file_intake(project_id, title, body, fingerprint="")`.
  It only files, or counts onto, a GitHub issue in a registered project's
  repository. It starts no goal and changes no code.
- **This narrows spec 046's "intake tools are gone" for FILING ONLY.** The
  grading, readiness gates and intake-driven dispatch stay deleted; the
  captain's 2026-11-16 ruling is that devclaw is the one door agents file
  through.
- Lives in `devclaw/server/tools/intake.py` (the goal layer is at its
  1,500-line ceiling and filing is protocol, not the tick rule).

## Decisions

- **Fingerprint**: the caller's `fingerprint` (trimmed, lowercased; kept as is
  when it is `[a-z0-9._-]{1,40}`, otherwise its sha256 prefix), else derived
  from `title`: lowercased, digit runs and hex ids collapsed to `#`, whitespace
  collapsed, sha256 prefix (12 hex). Stated in the tool description.
- **The key lives on the forge**: a label `intake:<fingerprint>` on the issue.
  Label filtering on the issues API is strongly consistent (search is not), and
  GitHub stays the state — the host stores nothing (pillar 5).
- **Count**: the issue body starts with `<!-- devclaw:intake fp=… count=N -->`
  and a visible `**Occurrences:** N` line; a repeat bumps both and posts a
  comment carrying the latest occurrence.
- **Closed**: a closed issue with the fingerprint is not reopened; a new issue
  is opened that names the closed one.
- **Concurrency**: one process-wide lock per (repo, fingerprint) around
  look-up + create/update. devclaw is the only writer through this door, so a
  single-process lock closes the check-then-create race.
- Any `gh` failure raises `ToolError`; nothing is reported filed that is not.

## Done when

- First filing opens one issue carrying the fingerprint label, count 1.
- A repeat adds a count line and one comment to the open issue and returns its
  URL and count; no second issue.
- The same fingerprint in another repo is a separate issue.
- A closed issue with the fingerprint yields a new issue referencing it.
- A concurrent double call yields one issue, count 2.
- The tool description says how to pass the repo (`project_id`) and the fingerprint.
