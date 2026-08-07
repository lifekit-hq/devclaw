# Commit hygiene

COMMIT your change yourself with a clean conventional-commit message — it becomes the PR a human reviews:

- **Subject:** `type(scope): what changed` (`feat`/`fix`/`refactor`/`test`/`docs`/`chore`; imperative, ≤ ~70 chars; the CHANGE, not the task).
- Blank line, then a 2–4 line body: WHY and how you verified.
- Resolving a tracked issue? Add `Fixes #<n>` so the PR links and closes it.

```
fix(feed): stop pagination drift on mid-scroll inserts

Cursor-encode the last-seen id + timestamp. Verified: dotnet test.

Fixes #42
```

ONE commit, staging everything. Do NOT push or open a PR — devclaw delivers it as a branch + PR.
