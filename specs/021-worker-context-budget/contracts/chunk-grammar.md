# Contract: chunk grammar (shared by host slice_guard and runner parser)

The chunk plan's typed contract is the speckit tasks.md checkbox grammar,
frozen here so the host parser (`devclaw/goal/slice_guard.py`) and the
runner's standalone mirror parser cannot drift apart. Shared test fixtures
exercise BOTH parsers against this file's examples.

## Grammar (unchanged from slice_guard today)

- Artifact path: `specs/<feature-dir>/tasks.md`, feature dir `NNN-*` (host
  regex `^specs/[^/]+/tasks\.md$`).
- Task row: a checkbox line `- [ ] T<nnn> …` / `- [x] T<nnn> …`
  (task id regex `T\d+`, case-insensitive `x`, tolerant of leading
  whitespace).
- Story tag: `[US<n>]` anywhere in the row (regex `US\d+`); rows without a
  story tag belong to the feature's shared/setup phase and are not a slice by
  themselves.
- Slice identity: distinct `(feature_dir, US<n>)`.
- Slice complete: every task row carrying that story tag is checked.

## Runner-side semantics (new)

- Session-start snapshot: checkbox state of every `specs/*/tasks.md` present
  in the workspace.
- Slice-advance: a slice not complete in the snapshot that is complete now.
- Stop condition: (slice-advance count ≥ 1) AND a subsequent write touches a
  task row OUTSIDE every advanced slice ⇒ end the turn (cancel + land-now).
  The worker's own wrap-up (commits, notes, honest re-flips) never triggers
  the stop by itself.
- Single-chunk fast path: no `specs/` tree, or ≤1 incomplete slice at
  session start ⇒ watcher disarmed (FR-005: zero ceremony).
- Observation cadence: the watcher RE-READS the files at tool-call
  boundaries (they are small; a stat/mtime gate provably misses a same-size
  `[ ]`→`[x]` flip inside one timestamp granule).
- An unreadable tasks.md contributes nothing to the runner's watcher (it
  disarms detection for that file — never guesses). The FR-004 loud block on
  a corrupt continuation artifact is enforced HOST-side at the dispatch
  boundary (`devclaw/goal/tick.py::_chunk_plan_corruption`, zero-LLM), where
  "this is a continuation" is known from settle records — before a session
  is burned, not inside one.

## Anti-drift

- `tests/` carries shared fixture files (valid, edge, corrupt) parsed by both
  implementations; a grammar change REQUIRES updating this contract + both
  parsers + fixtures in one PR.
