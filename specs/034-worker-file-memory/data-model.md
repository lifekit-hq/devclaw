# Data model — spec 034

## Added: the repo-resident memory (versioned with the code)

```
<repo>/.devclaw/
├── MEMORY.md            # always-read index — one line per fact + the write policy
└── memory/
    └── <slug>.md        # one durable repo fact per file: `# title` + prose body
```

- **Memory index** (`MEMORY.md`): the seed states the write policy in a
  short preamble, then a `## Facts` list; each entry is exactly
  `- [title](memory/<file>.md) — one-line hook`. Small by contract; never
  carries fact bodies. Advisory size smell at > 30 entries (doctor WARN).
- **Memory fact** (`memory/<slug>.md`): plain markdown — one `# title`
  heading and a free prose body. No frontmatter; nothing parses fact
  internals (clarified 2026-09-01).
- **Lifecycle**: created/updated/deleted by the worker inside an increment;
  reaches `main` through the increment's PR and merge-on-close; visible to
  other goals after the close (accepted latency, same as any change).
  Goal-scoped state (attempts, failure context, steering, decisions) never
  enters it — that stays in the dispatch brief.

Contract for readers/writers: [contracts/memory-layout.md](./contracts/memory-layout.md).

## Added: boilerplate revision 3

`project_manifest.BOILERPLATE_REVISION = 3`. A repo whose manifest says
< 3 is reported by `project.manifest.revision` (existing check) and the
migrate PR (`speckit_setup.migrate_manifest_pr`) seeds `.devclaw/MEMORY.md`
when absent, exactly as revision 2 seeds the `.goals/` gitignore line.

## Added: one doctor project finding

`project.worker_memory.health` — OK when `.devclaw/` is absent (not
seeded) or the index and the fact files agree; WARN (advisory, remedy:
curate by PR) naming: index lines whose file is missing, fact files not
indexed, an index past 30 entries. Never FAIL, never a hold.

## Removed (the hard cut)

| Thing | Where | Replacement |
|---|---|---|
| `REPO NOTES:` hand-back field | runner return contract + parser + result/blocked payload | none — the worker edits files instead |
| `PollResult.repo_notes` | `devclaw/goal/models.py` | none |
| `project_docs` table (kind `repo_brief`) | `devclaw/goal/state.py` DDL; `state_content` mixin; `store/content.py` | dropped at boot (`DROP TABLE IF EXISTS project_docs`) |
| `merge_repo_notes`, `render_brief_prefix`, `MAX_BRIEF_CHARS`, `scope_key_for` | `devclaw/goal/repo_brief.py` | `worker_memory_pointer(workspace_dir)` — a pure fs probe |
| settle-side writeback block | `devclaw/goal/tick_settle.py` | none |

## Dispatch brief composition after this spec (FR-009)

```
[ARCHITECTURE.md pointer — when present]
[.devclaw/MEMORY.md pointer — when present]
Advance this goal by one substantive, shippable increment using speckit, then stop.
<one line: the procedure is the speckit-artifacts skill in your standard instructions>
<saga framing (spec 012)>
<live referenced-issue contract (spec 019)>
<prior increments (spec 012)>
<decisions on this goal (spec 031)>
<failure context (spec 020/021)>
<steering>
```

The skill bundle (standard instructions) is prepended in-sandbox by the
runner from `runner/skills/` — the one home. Brief size is independent of
the number of stored facts.
