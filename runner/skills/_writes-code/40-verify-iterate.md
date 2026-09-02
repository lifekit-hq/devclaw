# Verify and iterate

Keep the change focused. Refactoring **what you touch** is in scope (splitting a god object you're editing is the work); refactoring code you didn't need to touch is out of scope.

One task ships ONE coherent, reviewable change. If the work grows beyond that, stop at the coherent slice you completed and report the remainder as a proposed split (FOLLOW-UPS) — never ship an unreviewable diff.

When done, VERIFY with the project's OWN tools and iterate until they pass: run its test/build command AND any linter, formatter, and type-checker (look in `package.json` scripts, `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.eslintrc` / `ruff` / `mypy` / `tsconfig`). Fix everything they flag, not only failing tests.

Bound every build/test run by the sandbox's DECLARED allocation: `DEVCLAW_SANDBOX_MEMORY` and `DEVCLAW_SANDBOX_CPUS` in your environment are the real limits. `/proc/meminfo` and `nproc` report the HOST machine, not your container. Cap test-runner workers to the declared CPUs (e.g. `maxWorkers`, `-n 2`), limit node heap (`NODE_OPTIONS=--max-old-space-size`), and run heavy suites serially. Prefer a slower run that stays inside the cap over a faster parallel one that risks the OOM killer; a command that dies with `Killed` hit the memory cap — bound it tighter and re-run, do not just retry it.

Finish with a short summary of what you changed and the checks you ran (tests + lint + types).
