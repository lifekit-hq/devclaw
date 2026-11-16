# TinySpec: `file_intake` accepts the envelope and returns the receipt

**Branch**: fm/iss-devclaw-1024
**Date**: 2026-11-16
**Status**: in review
**Complexity**: small (one tool module, its test file, one README line)
**Issue**: #1024 — extends #1023 (`specs/tiny/file-intake-idempotent.md`)

## North-star case

- **Failure moved**: *ran but needed the owner*. A filer that gets no receipt asks
  again or the owner chases it; a door that refuses an envelope with no `owner` or
  `acceptance` stops the owner reading under-specified issues.
- **Number that shows it**: filings that return `{id, url, state}` (all of them),
  and issues without acceptance (zero through this door).
- **Cut when**: no caller ever passes `thread`, or `rejected` receipts are never read.

## What

`file_intake` takes the envelope fields and returns the receipt, both as the
build-standards entry "Envelope and receipt at every door" (vault
`memory/system/build-standards.md`, 2026-11-16, decision 0016). That entry is the
one text; this repo cites it and does not restate it.

## Decisions

- Required: `owner` (must be the project's repo, or its `project_id`) and
  `acceptance`. `class` (arg `class_`, a Python keyword) defaults to `dev` and
  anything else is refused. `origin` and `thread` are optional.
- A refused envelope is a receipt, not an exception: `state: rejected`, empty
  `id`/`url`, `grade.reason`. Nothing is filed.
- States: `submitted` (new issue), `working` (a repeat that added a count).
- The issue body carries one fenced ```` ```devclaw-envelope ```` block after the
  count header; a repeat posts its `thread` in the occurrence comment.
- `id` is `owner/repo#number`.
- There is no grade or `regrade_intake` in v2 (removed with spec 046); "failed
  grade" is the envelope check above. The block is plain markdown, so nothing
  reads it back.

## Done when

- Tests cover a full envelope, a missing `owner`, a repeat (`working` with the
  count) and a rejected envelope.
- The tool description names the fields and the receipt; the README line says so.
- Host steps: image deploy.
