# The plan lives in the repo — `.devclaw/workflow.md`

Work outlives your session. **Read `.devclaw/workflow.md` first and follow it**: it is the repository's own manifest of how work is planned, built and shipped here. You bring no planning harness of your own — the repo's conventions win.

**No manifest? This is the first run on this repository.** Work it out the way a developer joining the project would — read its AGENTS.md / CLAUDE.md / README, look for planning tooling it already uses, read its package / project / solution scripts and its `.github/workflows` — then write ONE page at `.devclaw/workflow.md` and commit it with this increment:

- **Planning** — the harness the project uses and the exact commands to drive it. If it has none, say so: `none: plan in the PR description, one reviewable increment per session`. Don't install one.
- **Build / test / verify** — a pointer to `.devclaw/verify` plus anything that script can't run.
- **Conventions** — branch naming, commit format, PR expectations, where plans and docs live.
- **Traps** — a pointer to `.devclaw/memory/`.

If `.gitignore` ignores `.devclaw/`, un-ignore it in the same increment — `.gitignore` is a product file, not a gate input, and a manifest that never lands on the branch teaches the next session nothing. Keep ignoring the per-run scratch inside it (`.devclaw/playwright-report.json`): the manifest is tracked, the run artifacts are not.

Then, every run:

- **The branch is the ledger.** Continue what THIS branch added — its commits, its PR description, the planning artifacts the manifest names. You cannot adopt another goal's plan.
- Take the smallest not-yet-done piece; one coherent reviewable increment per session.
- Record load-bearing choices (a stack, a schema, an API shape) with a one-line why where the manifest says plans live, so no session relitigates them.
- Correct the manifest in place when a change makes it wrong. Never append to it, and never create a root `PLAN.md`.
