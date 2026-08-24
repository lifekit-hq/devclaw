# Changelog

All notable changes to `spec-kit-trace` will be documented in this file.

## [1.0.0] — 2026-05-12

### Added

- `/speckit.trace.build` — scan `spec.md` and the test suite, write `.specify/trace.md`
- `/speckit.trace.gaps` — list untested requirements with derived severity and suggested test names
- `/speckit.trace.orphans` — list tests whose requirement IDs no longer exist in `spec.md`, with rename hints
- `/speckit.trace.report` — compact one-page report with CI-grep summary lines (`TRACE-COVERAGE`, `TRACE-GAPS`, `TRACE-ORPHANS`)
- Language-agnostic `REQ-XXX` token matching (Python, JS/TS, Go, generic)
- Optional JSON output for `/speckit.trace.report`
