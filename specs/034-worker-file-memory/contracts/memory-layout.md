# Contract: the `.devclaw/` worker memory layout

Any agent — devclaw's worker, a human, another harness — may read and write
this directory. It is plain markdown; discovery is `ls` + `cat`.

## Files

- `.devclaw/MEMORY.md` — the index. Always read at session start. Contains
  a short policy preamble and a `## Facts` section. One line per fact:

  ```
  - [<title>](memory/<file>.md) — <one-line hook>
  ```

  The link target is relative to `.devclaw/`. Nothing else in the file is
  parsed.

- `.devclaw/memory/<slug>.md` — one durable repo fact per file:

  ```
  # <title>

  <free prose: what a fresh session must not relearn the hard way>
  ```

  No frontmatter. File names are kebab-case slugs.

## Write policy (stated in the seeded index; enforced by review, not code)

1. One fact per file. Update the existing file over adding a reworded sibling.
2. Delete a fact proven wrong. Deletion is normal.
3. Never record goal-scoped state (what this goal attempted, why the last
   attempt failed, steering, decisions) — that rides the dispatch brief.
4. Mechanize first: a lesson a committed mechanism can enforce (a wrapper
   script, a config default, a guard) becomes that mechanism, with at most a
   pointer here.
5. Memory edits ship in the same commit/PR as the increment; an
   uncommitted edit does not survive the sandbox.

## What the harness does with it

- Dispatch: if `MEMORY.md` exists in the worker's checkout, the brief carries
  one pointer line. No fact body is ever injected.
- Onboard / migrate PR: seeds an empty `MEMORY.md` (policy + empty list) when
  absent. Never overwrites an existing one.
- Doctor: `project.worker_memory.health` WARNs on a dangling index line, an
  unindexed fact file, or more than 30 entries. Advisory only.
