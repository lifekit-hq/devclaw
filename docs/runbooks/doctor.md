# Doctor — the post-redeploy checklist as one verb

**Status**: CURRENT — written with spec 016 US1 (2026-08-24); US3 drift checks added same day.

After **every** redeploy (and any time the instance feels off), run the
doctor before trusting the box:

```bash
# via MCP (waiter): call the `doctor` tool, optionally doctor(project_id=…)
# via CLI on the box (no server needed):
python -m devclaw.cli doctor            # human table
python -m devclaw.cli doctor --json     # machine shape (same as the MCP tool)
python -m devclaw.cli doctor --project finance-sentry
```

Doctor is **read-only and zero-LLM**: it mutates nothing, dispatches nothing,
and never invokes `claude`. Every non-ok finding names the existing verb that
fixes it — doctor executes none of them.

## What it checks (v1, spec 016 US1)

Later check families, by home — instance (`devclaw/doctor/checks_instance.py`):
registry token, gate consultation, schedule dispatch, pr_ledger, sandbox
sizing, problems/decisions tables, merge-on-close columns, contract pins;
project (`devclaw/doctor/checks_project.py`): tracked checkout, issue-ref
shape, backlog-ready contract, capability declaration, `.goals/` ignored,
worker-memory health (`project.worker_memory.health`, spec 034 — advisory:
a dangling `.devclaw/MEMORY.md` index line, an unindexed fact file, or an
index past 30 entries WARNs; nothing is dropped). The instance legacy-shape
check also FAILs on a lingering `project_docs` table (dropped at boot).

Instance section:

- legacy row shapes the boot migrations should have erased (the marker
  modules were deleted; checks guard shapes directly: pre-008 `lifecycle`,
  NULL/nullable `goal_deliveries.ref_id`, lingering `goal_docs` /
  `inbox_ingest_cursor`)
- OAuth credential file presence + `expiresAt` horizon (fails before the
  2 a.m. auth death, mechanically — no live probe), `.claude.json` identity,
  `CLAUDE_CODE_OAUTH_TOKEN` presence, active usage/auth pause
- skills bundle resolvability for every kind in `runner._KNOWN_KINDS` (the host-side
  pre-dispatch check the `skills_missing` class never had, #610/#613)
- run-window **raw** meta key: absent (lost on redeploy) vs corrupt vs valid —
  `get_run_schedule` alone cannot tell "operator disabled" from "row gone"
- `DEVCLAW_SELF_REPO` unset **while the problems catalog already holds
  worker-reported environment deficiencies** (spec 038 US2) — those gaps held
  a project each and none was filed as devclaw work. Keyed on the catalog so
  it fires on the instance losing filings, not on every dev checkout

Per-project section:

- workspace dispatchability (the spec-003 preflight, surfaced)
- dangling advisory `goal_ids` links (cancel+refile drift — invisible to
  rollups until doctor)
- goals on the project's workspace missing their `project_id` stamp
- `devclaw.json` presence / validity / boilerplate-revision currency (US3 —
  the remedy is re-`onboard`, which opens the seed/migrate PR; see
  [`../reference/devclaw-manifest.md`](../reference/devclaw-manifest.md))
- `devclaw:managed` marker integrity in AGENTS.md, and `.specify/` scaffold
  drift against the packaged canonical source (the #610 fork class, per repo)

## Exit codes (CLI)

`0` healthy or warns only · `1` any fail/unknown · `2` unknown `--project`.

## Extending it

A PR that changes persisted state shape or in-repo boilerplate ships its
doctor check (spec 016 FR-014) — add the check function to
`devclaw/doctor/checks_instance.py` / `checks_project.py`, register it in the
ordered tuple, and land a seeded-fault test in `tests/test_doctor.py`.
