# Repo memory — `.devclaw/`, edited like code

A durable repo fact a future session on a DIFFERENT task would need (a build quirk, a test gotcha, an environment trap) goes in the repo, not your summary: one fact per file at `.devclaw/memory/<slug>.md` (`# title` + prose), listed once in `.devclaw/MEMORY.md` as `- [title](memory/<slug>.md) — one-line hook`.

- **Update over append**: edit the existing fact in place, never add a reworded sibling; delete a fact you proved wrong.
- **Mechanize first**: a lesson a committed wrapper, config default or guard can enforce becomes that mechanism, with at most a pointer here.
- **Never goal-scoped state** (what this goal attempted, why the last run failed, steering) — that rides the brief.
- Commit memory edits with the increment; an uncommitted edit dies with the sandbox. No `.devclaw/` yet? Create it with the first fact.
