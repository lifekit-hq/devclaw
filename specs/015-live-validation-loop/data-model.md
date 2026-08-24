# Data model — spec 015 (live-validation loop)

## ValidationContract (parsed from `devclaw.json` → `validation`)

| Field | Type | Rules |
|---|---|---|
| `boot` | `str` | mandatory; a command that boots the hermetic seeded instance and exits 0 only when it is up (seeding + readiness-wait are the script's job) |
| `suites` | `str` | mandatory; runs the accumulated acceptance suites against the running instance; writes the Playwright JSON report when browser suites run |
| `smoke_path` | `str` | optional, default `/`; the read-only path the post-deploy prod smoke GETs (wire key `smokePath`) |

Parsed fail-loud inside `parse_manifest` (a present-but-malformed `validation`
raises `ManifestError`); absent ⇒ `None` (repo not opted into validation).
Resolver: `resolve_validation_contract(workspace_dir) -> ValidationContract | None`
reading the merged base like every other manifest consumer.

## validation_report (runner result field, `validate_product` runs only)

```json
{
  "contract_ran": true,
  "boot": {"passed": true, "exit_code": 0, "output_tail": "..."},
  "suites": {"ran": true, "passed": false, "exit_code": 1, "output_tail": "..."},
  "browser_report": {"expected": 12, "unexpected": 2, "flaky": 0, "skipped": 1},
  "failing_tests": ["checkout > coupon applies", "jobs > sentinel registers"],
  "partial": false,
  "note": ""
}
```

- `failing_tests`: titles extracted recursively from the Playwright JSON
  report's suites tree (`outcome == "unexpected"`); empty when no report.
- `partial: true` + `note` when the wall clock cut suites (explicit partial
  coverage — silent truncation forbidden).
- Green-by-vacuity (`suites` passed, 0 executed) sets
  `note: "green-by-vacuity: no acceptance tests accumulated yet"`.

## Finding mapping (host, `goal/validation.py`)

| Condition | fingerprint | severity | spec_ref |
|---|---|---|---|
| failing test title T | `validator\|{T}` | `high` | T |
| suites exit ≠ 0, no parseable report | `validator\|suite-exit` | `high` | — |
| boot failed | `validator\|boot` | `critical` | — |
| contract missing on a triggered run | `validator\|missing-contract` | `high` | — |
| prod smoke non-2xx/3xx or unreachable | `deploy_smoke\|{slug}\|{path}` | `critical` | — |

All file through `issue_doorway.file_finding` (spec 014) against the
project's repo slug; dedup/occurrence/reopen semantics are 014's.

## QA goal

A `Goal` with `mode="qa"`:
- `done_when` = the standing contract text (satisfies `is_standing()`), set
  at creation.
- Never planned by the tick (no advance dispatch, no done-gate on settled
  validation results — they append run-record log lines instead).
- Excluded from the single-writer project-hold derivation, and its dispatches
  are not blocked by the hold (validation is read-only toward the repo and
  runs in the qa goal's own `workspace_dir`).
- `cadence`: empty ⇒ periodic runs disarmed (the shipped default); set ⇒
  a due + inside-run-window tick enqueues one validation run (mechanical).

## Run record

No new table. One goal-log line per run
(`validation: green (12 executed)` / `validation: 2 findings filed (#12, #13)` /
`validation: partial — cut after suite X` / boot-failed variants) plus the
standard task events. The owner sees it via `tail_goal` / the console.
