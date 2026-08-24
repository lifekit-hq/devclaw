# spec-kit-trace

A [Spec Kit](https://github.com/github/spec-kit) extension that builds a **requirement → test traceability matrix** from `spec.md` and the test suite, surfaces untested requirements, and flags orphan tests whose requirement IDs have disappeared from the spec.

## Problem

Spec-Driven Development treats `spec.md` as the source of truth, but nothing in the default workflow links a requirement to the tests that prove it works:

- A requirement can ship with zero covering tests and nobody notices until it breaks in production.
- A requirement can be renamed or removed, leaving orphan tests that pass but verify nothing real.
- Coverage tools measure lines of code, not behaviour described in the spec.
- CI cannot gate a PR on "every new requirement has at least one test".

Traceability is the missing link between *what we said we'd build* and *what we proved we built*.

## Solution

`spec-kit-trace` adds four read-only commands that turn `REQ-XXX` identifiers in `spec.md` and the test suite into a deterministic traceability matrix:

| Command | Purpose | Modifies Files? |
|---------|---------|-----------------|
| `/speckit.trace.build` | Build the matrix and write `.specify/trace.md` | Writes one file |
| `/speckit.trace.gaps` | List untested requirements with severity | No — read-only |
| `/speckit.trace.orphans` | List tests whose requirement IDs are no longer in the spec | No — read-only |
| `/speckit.trace.report` | Compact, CI-friendly one-page report | No — read-only |

## Installation

```bash
specify extension add --from https://github.com/Quratulain-bilal/spec-kit-trace/archive/refs/tags/v1.0.0.zip
```

## How It Works

### The Traceability Gap

```
/speckit.specify  → spec.md          Requirements live here (REQ-001, REQ-002, ...)
                   → tests/          Tests live here

                   → ???             Which test covers which requirement?

/speckit.trace.*  → .specify/trace.md ← spec-kit-trace answers this
```

### Annotating Tests

`spec-kit-trace` does not require a new framework. It looks for `REQ-XXX` tokens anywhere inside a test file. Use whatever feels natural for your language:

**Python**

```python
def test_password_reset_link_expires_after_one_hour():
    """REQ-004: link must expire after 1h."""
    ...
```

**JavaScript / TypeScript**

```ts
// REQ-002: sessions expire after 24h
it("expires sessions after 24 hours", async () => { ... });
```

**Go**

```go
// REQ-007: audit log retention is 90 days
func TestAuditLogRetention(t *testing.T) { ... }
```

A single test may reference multiple requirements; a single requirement may be covered by multiple tests. The matrix lists every relationship.

### What Gets Produced

**`/speckit.trace.build`** writes `.specify/trace.md`:

```markdown
| Requirement | Covering Tests | Status |
|-------------|----------------|--------|
| REQ-001 | test_signup_with_valid_email | ✅ Covered |
| REQ-002 | test_session_expires_after_24h | ✅ Covered |
| REQ-003 | — | ⚠️ Gap |
```

**`/speckit.trace.report`** prints a CI-ready summary:

```text
TRACE-COVERAGE: 87
TRACE-GAPS: 2 (1 high, 0 medium, 1 low)
TRACE-ORPHANS: 1
```

CI pipelines can grep those three lines without parsing JSON.

## Use Cases

- **Pre-merge gate** — fail PRs that introduce a new `REQ-XXX` without a covering test.
- **Refactor safety** — when renaming a requirement, run `/speckit.trace.orphans` to find every test that still references the old ID.
- **Compliance reporting** — `/speckit.trace.report --format=json` emits a structured artifact for audit trails.
- **Spec hygiene** — periodic runs surface dead requirements (no tests, no code references).

## Design Choices

- **Read-only by default.** Only `/speckit.trace.build` writes, and only to `.specify/trace.md`. Everything else reports.
- **No magic name matching.** A test covers a requirement only if its file contains the literal `REQ-XXX` token. No fuzzy matching on test names — that's how you get silent false positives.
- **Language-agnostic.** Works with any test runner because matching happens on the text of the test file, not on the runner's metadata.
- **CI-first output.** Every command prints a stable `TRACE-*:` summary line so pipelines can act on it without JSON parsing.

## Requirements

- Spec Kit `>= 0.4.0`
- A project with `.specify/spec.md` and at least one test file

## License

MIT
