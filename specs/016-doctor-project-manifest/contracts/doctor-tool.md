# Contract: `doctor` — MCP tool + CLI subcommand

## MCP tool

```
doctor(project_id: Optional[str] = None) -> str   # JSON string, like every devclaw tool
```

- `project_id=None`: full report — instance section + every registered
  project's section.
- `project_id="x"`: instance section + that project's section only
  (unknown project ⇒ ToolError, matching `_resolve_project_or_reject`).
- **Read-only**: no store writes, no dispatch, no container, no cognition
  call, not reachable from the tick path.

## Response shape

```json
{
  "healthy": false,
  "counts": {"ok": 14, "warn": 2, "fail": 1, "unknown": 0},
  "findings": [
    {
      "check_id": "instance.schedule.raw_key",
      "verdict": "fail",
      "evidence": "meta key 'run_schedule' absent — window lost (redeploy?); dispatch currently ungated",
      "remedy": "set_run_schedule",
      "project_id": null
    },
    {
      "check_id": "project.links.dangling",
      "verdict": "warn",
      "evidence": "goal_ids entry 'g-old' resolves to no goal (cancel+refile drift)",
      "remedy": "link_goal",
      "project_id": "finance-sentry"
    }
  ]
}
```

Guarantees:
- Every registered check appears exactly once per applicable scope — a check
  that crashed appears with `verdict: "unknown"` and the error as evidence;
  none is ever omitted.
- `healthy: true` ⇒ `findings` still lists every check as `ok`
  (affirmative health, no empty-output ambiguity).
- Deterministic ordering (instance declared order, then projects by id) and
  timestamp-free evidence ⇒ unchanged state produces byte-identical output.
- Non-ok findings always carry a non-empty `remedy` naming an existing verb.

## CLI

```
devclaw doctor [--project <id>] [--json]
```

- Default: human table (check id, verdict glyph, evidence, remedy),
  exit code 0 when healthy, 1 when any `fail`/`unknown`, 0 with output when
  only warns.
- `--json`: the exact MCP response shape on stdout.
- Uses the standard `main()` registry+goals path (works without the server
  running, like the rest of the CLI).

## Non-goals (v1)

- No `--fix`/auto-heal flag — remedies are named, never executed.
- No deploy-gating semantics; report-only.
