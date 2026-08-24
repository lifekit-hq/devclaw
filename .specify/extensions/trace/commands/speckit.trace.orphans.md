---
description: "List tests that reference a requirement ID that no longer exists in spec.md"
---

# Trace Orphans

Find tests that annotate a `REQ-XXX` identifier which is missing from the current `.specify/spec.md`. Orphan tests usually mean a requirement was renamed, split, merged, or dropped — and the test was never updated.

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty). The user may specify:
- Test directory override
- A rename hint (e.g., "REQ-010 was renamed to REQ-AUTH-001")
- An ignore list for known dropped requirements

## Prerequisites

1. Run the scan from `/speckit.trace.build`.
2. Build two sets:
   - `spec_ids` — every `REQ-XXX` present in `.specify/spec.md`.
   - `test_ids` — every `REQ-XXX` referenced anywhere in the test suite.

## Outline

1. **Compute Orphans**: `orphans = test_ids - spec_ids`.

   ```markdown
   ## Orphan Tests

   | Test | File:Line | References | Reason |
   |------|-----------|------------|--------|
   | test_old_signup_flow | tests/test_auth.py:120 | REQ-LEGACY-001 | Not in spec |
   | test_admin_delete | tests/test_admin.py:55 | REQ-010 | Not in spec |
   ```

2. **Rename Detection**: If the user provided a rename hint, surface it. Otherwise, suggest likely renames using a name-similarity heuristic on the requirement descriptions in git history.

   ```markdown
   ## Likely Renames

   | Orphan | Best Match | Confidence |
   |--------|------------|------------|
   | REQ-010 | REQ-AUTH-001 | High (description match) |
   | REQ-LEGACY-001 | — | None — likely dropped |
   ```

3. **Action Recommendation**: For each orphan, recommend one of: rename annotation, delete test, or move test to a legacy suite.

   ```markdown
   ## Recommended Actions

   - test_admin_delete → update annotation to REQ-AUTH-001
   - test_old_signup_flow → delete or move to tests/legacy/
   ```

4. **Exit Signal**: Print a final CI-grep line.

   ```text
   TRACE-ORPHANS: 2 orphan test(s)
   ```

## Rules

- **Read-only** — never modify tests or spec files; recommendations are reported, not applied.
- **No silent deletes** — never suggest deleting a test without flagging it for human review.
- **Confidence required for rename** — only suggest a rename when the description match is strong; otherwise mark "dropped".
- **Preserve test history** — recommend moving to `tests/legacy/` over deletion when the behavior may still ship.
- **CI-friendly summary** — final `TRACE-ORPHANS:` line must always print so pipelines can grep for it.
