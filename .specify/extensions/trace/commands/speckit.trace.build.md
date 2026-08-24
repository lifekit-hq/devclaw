---
description: "Build a requirement → test traceability matrix from spec.md and the test suite"
---

# Trace Build

Scan `spec.md` for tagged requirements (`REQ-XXX`) and the test suite for traceability annotations, then generate a structured matrix linking every requirement to its covering tests.

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty). The user may specify:
- A test directory to scan (e.g., "tests/", "src/__tests__")
- A requirement ID prefix to filter (e.g., "only REQ-AUTH-*")
- An output path (e.g., "write to .specify/trace.md")
- A test file glob (e.g., "*.spec.ts", "test_*.py")

## Prerequisites

1. Confirm `.specify/spec.md` exists in the project.
2. Identify the test directory:
   - If user specified one, use it.
   - Otherwise auto-detect: `tests/`, `test/`, `__tests__/`, `spec/` — first one that exists.
3. Identify language conventions:
   - Python: `# REQ-XXX` or `@pytest.mark.requirement("REQ-XXX")`
   - JS/TS: `// REQ-XXX` or `it.requirement("REQ-XXX", ...)`
   - Generic: `REQ-XXX` anywhere in a test file's content

## Outline

1. **Scan Requirements**: Extract every `REQ-XXX` identifier from `.specify/spec.md`.

   ```markdown
   ## Requirements Found

   | ID | Description | Section |
   |----|-------------|---------|
   | REQ-001 | User can sign up with email/password | Authentication |
   | REQ-002 | Sessions expire after 24h | Authentication |
   | REQ-003 | Admin dashboard shows active users | Admin |
   ```

2. **Scan Tests**: For each test file, record every `REQ-XXX` reference and the test name(s) on the same construct.

   ```markdown
   ## Tests Found

   | Test | File:Line | References |
   |------|-----------|------------|
   | test_signup_with_valid_email | tests/test_auth.py:42 | REQ-001 |
   | test_session_expires_after_24h | tests/test_auth.py:88 | REQ-002 |
   | test_admin_dashboard_renders | tests/test_admin.py:15 | REQ-003 |
   ```

3. **Build the Matrix**: For every requirement, list its covering tests.

   ```markdown
   ## Traceability Matrix

   | Requirement | Covering Tests | Status |
   |-------------|----------------|--------|
   | REQ-001 | test_signup_with_valid_email | ✅ Covered |
   | REQ-002 | test_session_expires_after_24h | ✅ Covered |
   | REQ-003 | test_admin_dashboard_renders | ✅ Covered |
   | REQ-004 | — | ⚠️ Gap |
   ```

4. **Summary**: Report coverage totals.

   ```markdown
   ## Coverage Summary

   | Metric | Value |
   |--------|-------|
   | Requirements total | 8 |
   | Requirements covered | 6 |
   | Coverage | 75% |
   | Orphan tests | 2 |
   ```

5. **Output**: Write the matrix to `.specify/trace.md` (or the user-specified path) and print a one-paragraph summary in the chat.

## Rules

- **Read-only on source** — never modify `spec.md` or any test file.
- **Single write target** — only `.specify/trace.md` (or the user's path) is written.
- **Deterministic** — the same inputs must produce the same matrix.
- **Language-agnostic IDs** — match `REQ-XXX` as a token (letters, digits, dashes) regardless of comment syntax.
- **Don't infer** — never guess that a test covers a requirement based on names alone; require an explicit `REQ-XXX` annotation.
- **Surface ambiguity** — if a test references multiple requirements, list it under each; if a requirement has no test, mark it as a Gap (not "Unknown").
