# Data model — spec 014 (issue doorway)

## MachineFinding (input — frozen dataclass in `devclaw/issue_doorway.py`)

| Field | Type | Rules |
|---|---|---|
| `source` | `str` | mandatory; which mechanism found it (`self_issue_catalog`, `deploy_smoke`, `validator`, …) — free string, non-empty |
| `fingerprint` | `str` | mandatory; stable dedup key, produced by the SOURCE (the doorway never derives one — cf. `state_store/problems.py::fingerprint_for` for the catalog's) |
| `evidence` | `str` | mandatory; what was run and what it showed; truncated at 6,000 chars with explicit marker (D7) |
| `expected` | `str` | mandatory; `"unknown"` is an explicit stated value, never empty (FR-001) |
| `actual` | `str` | mandatory; same rule |
| `severity` | `str` | mandatory; one of `critical`/`high`/`medium`/`low` — validated, invalid input rejected loudly at the doorway (never silently coerced) |
| `proposed_done_when` | `str` | mandatory; ≥ 20 chars (the admission `vague_done_when` bar, so a machine-filed issue can be dispatched without edit — SC-003) |
| `title` | `str` | mandatory; rendered as `[machine] <source>: <title>`, capped 240 chars |
| `spec_ref` | `str \| None` | optional; spec-scenario reference when the source is the spec-015 validator (FR-001) — rendered inside Expected vs actual |

Construction validates all rules and raises `ValueError` naming every problem
at once (the `intake.validate_shape` shape).

## FilingOutcome (output)

| Field | Type | Meaning |
|---|---|---|
| `action` | `str` | `filed` (new issue) / `updated` (occurrence on open issue) / `reopened` (recurrence after close) / `failed` |
| `issue_number` | `int \| None` | set on the three success actions |
| `reason` | `str` | on `failed`: the actionable cause (gh stderr tail) |

## machine_issues (SQLite ledger — `StateStore`, single writer)

```sql
CREATE TABLE IF NOT EXISTS machine_issues (
    repo            TEXT NOT NULL,      -- owner/name slug
    fingerprint     TEXT NOT NULL,
    issue_number    INTEGER NOT NULL,
    issue_state     TEXT NOT NULL,      -- 'open' | 'closed'
    schema_version  INTEGER NOT NULL,
    source          TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_ms   INTEGER NOT NULL,
    last_seen_ms    INTEGER NOT NULL,
    PRIMARY KEY (repo, fingerprint)
);
```

Store mixin (`state_store/machine_issues.py`):
`machine_issue_get(repo, fingerprint)`,
`machine_issue_record(repo, fingerprint, *, issue_number, issue_state, source, schema_version, now_ms)`
(insert-or-occurrence-bump), `machine_issue_set_state(repo, fingerprint, state)`.

## State transitions

```
(no row) --file_finding--> filed(open, occ=1)
open     --same fp------->  updated(open, occ+1)         # comment, no new issue
closed   --same fp------->  reopened(open, occ+1)        # recurrence comment + reopen
open     --external close-> ledger updated lazily on next recurrence
                            (the doorway trusts GitHub's actual reopen/close
                             result and reconciles the ledger from it — an
                             owner closing an issue by hand is discovered when
                             the fingerprint next fires: `gh issue reopen` on
                             an already-open issue is a no-op, so drift is
                             self-correcting, never wedging)
```

## Relationship to existing tables

- `problems` keeps its own `issue_number`/`issue_state` linkage; after US3 the
  catalog path calls `file_finding()` and mirrors the outcome into
  `set_problem_issue` exactly as today. The `machine_issues` row is written
  too (the catalog becomes just another producer).
- No change to `goal_*` tables, tasks, or events.

## Issue body (the external contract)

See [contracts/issue-schema.md](./contracts/issue-schema.md).
