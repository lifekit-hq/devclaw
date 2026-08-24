# Contract — the repo-declared validation contract (spec 015)

The externally visible interface a repo opts into the live-validation loop
with. Lives in `devclaw.json` (spec 016's manifest — the one repo-owned,
PR-reviewed config doorway), under one new optional key:

```json
{
  "$schema": "…",
  "schemaVersion": 1,
  "validation": {
    "boot": "./scripts/validate-boot.sh",
    "suites": "./scripts/validate-suites.sh",
    "smokePath": "/health"
  }
}
```

## Semantics

- **`boot`** (required): boots the product as a hermetic, seeded instance —
  seeding, port selection, and readiness-waiting are the script's
  responsibility; it exits `0` only when the instance is up. The sandbox runs
  `--network host`, so the script must pick ports that cannot collide with
  the host's deploy range (8200–8399) — document the chosen port for
  `suites` (an env var or a file the repo owns).
- **`suites`** (required): runs the accumulated acceptance suites against the
  running instance. Browser suites use the existing Playwright plumbing
  (`--reporter=json`; the runner exports `PLAYWRIGHT_JSON_OUTPUT_NAME` at
  `.devclaw/playwright-report.json`, which is also how failing scenarios get
  per-title identity in filed findings). Exit `0` = green.
- **`smokePath`** (optional, default `/`): the read-only path the
  post-deploy production smoke GETs. Production receives NOTHING else —
  e2e suites never run against it.

## Guarantees the loop gives the repo

- A validation run never opens a PR, never commits, never mutates the repo
  (boot/seed artifacts are discarded after the run).
- Every failure arrives as ONE spec-014 machine-filed issue per failing
  scenario, deduplicated by fingerprint across runs.
- A missing/malformed contract on a triggered run is loud: the run fails
  with an actionable reason and the missing contract is itself filed.
- Absent `validation` key = not opted in: deploys trigger nothing.
