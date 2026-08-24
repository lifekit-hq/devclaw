# Contract — machine-filed issue body, schema v1

The externally visible interface of the doorway: what the owner reads, what
the intake grader and any future consumer parses. Versioned in-band (FR-004);
this file is the normative definition of v1.

## Title

```
[machine] <source>: <title>
```

Capped at 240 characters.

## Body (canonical section order — FR-001, US1 scenario 1)

```markdown
> Machine-filed by devclaw via the issue doorway. This issue is the durable,
> gradeable record of a machine-found problem; dispatch stays human-gated.

<!-- devclaw-machine-issue v1 fingerprint=<fp> source=<source> severity=<severity> -->

## Source

<which mechanism found it, and where it ran>

## Evidence

<what was run and what it showed — fenced block(s); deterministically
truncated at 6,000 chars with `… [truncated: N chars omitted]`>

## Expected vs actual

- **Expected:** <expected — the literal string `unknown` when not meaningful>
- **Actual:** <actual — same rule>
- **Spec scenario:** <spec_ref, only when the source provides one>

## Severity

`critical` | `high` | `medium` | `low`

## Proposed done-when

<a draft completion contract a fixing goal could adopt verbatim; ≥ 20 chars>
```

## Machine-extraction rules

- The metadata line matches
  `^<!-- devclaw-machine-issue v(?P<version>\d+) fingerprint=(?P<fp>\S+) source=(?P<source>\S+) severity=(?P<sev>\S+) -->$`
  — one regex, no heuristics (FR-003). `source` is whitespace-free by
  construction (validated at `MachineFinding`); `fingerprint` is free-form at
  the producer (the catalog's carries spaces) and is **percent-encoded**
  (`urllib.parse.quote(fp, safe="")`) into the metadata line — consumers
  unquote it back.
- Sections are `## `-headed, in the order above, all always present:
  a field with no meaningful value carries the literal string `unknown`
  (absent-but-stated, never omitted).
- Parsers MUST dispatch on the version number before assuming section
  semantics (US1 scenario 2).

## Labels

- `devclaw:machine-filed` — always present (the doorway marker).
- Caller pass-through labels — e.g. the migrated catalog path keeps
  `devclaw:self-filed` + `class:<category>` unchanged.

## Occurrence comment (US2 — same fingerprint, open issue)

```markdown
**Occurrence** <n> — <ISO-8601 UTC>

<fresh evidence, same truncation rule>
```

## Recurrence comment (US2 — same fingerprint, closed issue; issue is reopened)

```markdown
**Recurrence** (regression) — previously closed; occurrence <n>, <ISO-8601 UTC>

<fresh evidence, same truncation rule>
```
