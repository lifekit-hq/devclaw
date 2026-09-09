# Commit hygiene

Commit your change with a clean conventional-commit message — it becomes the PR a human reviews:

- **Subject:** `type(scope): what changed` (`feat`/`fix`/`refactor`/`test`/`docs`/`chore`; imperative, ≤ ~70 chars; the CHANGE, not the task). Scope is a component, never a bare number (`fix(sentinels)`, not `fix(044)` — a number there names no namespace).
- Blank line, then a 2–4 line body: WHY and how you verified.
- Then a `Judgment calls:` section — the decisions a reviewer should weigh: defaults you chose, trade-offs taken, deviations from existing patterns. Omit the section only when there were none.
- Resolving a tracked issue? Add `Fixes #<n>` so the PR links and closes it.

```
fix(feed): stop pagination drift on mid-scroll inserts

Cursor-encode the last-seen id + timestamp. Verified: dotnet test.

Judgment calls: cursor rides the query string (precedent: /search filters);
kept the offset param for back-compat.

Fixes #42
```

Prefer one commit. Do NOT push or open a PR — devclaw delivers it as a branch + PR. Gate inputs and binaries are never yours to change (see the typed-blocks skill).

This is about the MESSAGE, not completeness: devclaw captures everything in the workspace either way, so anything unstaged is reviewed and shipped regardless. A good commit buys a reviewer who can see what you decided and why.
