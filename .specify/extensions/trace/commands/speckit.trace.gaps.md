---
description: "List every requirement in spec.md that has no covering test"
---

# Trace Gaps

Report the set of requirements that appear in `.specify/spec.md` but have no matching test in the test suite. Designed to fail loudly in CI when new requirements ship without tests.

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty). The user may specify:
- A severity threshold (e.g., "fail on any gap", "warn only")
- A requirement scope filter (e.g., "only REQ-CORE-*")
- An exclude list (e.g., "ignore REQ-DOC-*" for documentation-only requirements)

## Prerequisites

1. Run the same scan logic as `/speckit.trace.build`:
   - Extract every `REQ-XXX` from `.specify/spec.md`.
   - Scan the test directory for `REQ-XXX` annotations.
2. If `.specify/trace.md` exists and is fresher than `spec.md`, you MAY use it as a cache; otherwise rescan.

## Outline

1. **Identify Gaps**: For every requirement with no covering test, list it.

   ```markdown
   ## Untested Requirements

   | ID | Description | Section | Severity |
   |----|-------------|---------|----------|
   | REQ-004 | Password reset link expires after 1h | Authentication | High |
   | REQ-007 | Audit log retention is 90 days | Compliance | High |
   | REQ-012 | Footer shows copyright year | UI | Low |
   ```

2. **Severity Heuristic** (do not invent severity; derive from spec section):
   - High: requirements under sections matching `auth|security|compliance|payment|data`
   - Medium: requirements under `api|workflow|integration`
   - Low: everything else (UI copy, cosmetic, docs-only)

3. **Recommendation**: For each gap, suggest one concrete test name following the project's existing convention.

   ```markdown
   ## Suggested Tests

   - REQ-004 → `test_password_reset_link_expires_after_one_hour`
   - REQ-007 → `test_audit_log_retention_is_ninety_days`
   - REQ-012 → `test_footer_shows_current_copyright_year`
   ```

4. **Exit Signal**: Print a final line designed for CI grep.

   ```text
   TRACE-GAPS: 3 untested requirement(s) (2 high, 0 medium, 1 low)
   ```

## Rules

- **Read-only** — never modify spec files or tests; never auto-generate test stubs.
- **No false positives** — only flag requirements that have zero `REQ-XXX` references in the test corpus.
- **Severity is derived, not declared** — never write severity back into `spec.md`.
- **Suggest, don't write** — test name suggestions are in the report only.
- **Stable IDs** — if a requirement was previously covered and is now flagged, note "(regression)" beside it.
- **CI-friendly summary** — the final `TRACE-GAPS:` line must always be present so pipelines can grep for it.
