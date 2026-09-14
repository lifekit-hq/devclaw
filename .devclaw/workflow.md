# How work is done in this repository

The manifest a devclaw session reads first. Correct it in place when a change
makes it wrong; never append. `CLAUDE.md` is the full contract — this page is
the operating summary.

## Planning

**Harness: speckit**, vendored at `.specify/` (scripts, templates, and the
constitution the pipeline checks specs against). Specs live at `specs/` in the
repo ROOT, never under `.specify/`.

```
.specify/scripts/bash/create-new-feature.sh --short-name <slug>
```

then specify → clarify → plan → tasks, committed with the change. Every spec
opens by naming the north-star failure it moves (stopped when it shouldn't /
ran and produced garbage / ran but needed the owner) and its cut condition —
no case, no spec (`.claude/rules/speckit-workflow.md`).

Below spec size — bug fixes, mechanical refactors, one bounded PR — use the
**tinyspec lane**: ONE file at `specs/tiny/<name>.md` (What / Context /
Requirements / Plan / Tasks / Done-When), committed with the change.
Docs-only and test-only changes need no artifact.

Slice for reviewability, never for scope: each increment is one coherent PR,
but the whole spec is the commitment.

## Build / test / verify

`./.devclaw/verify` runs what CI gates on a PR: the pytest suite, `ruff`,
`mypy`, and `lint-imports`. It skips gitleaks (network-dependent) and yamllint
(CI runs it non-gating) — the script says so at the top.

The suite is **fully stubbed** — no docker, no `claude` binary. It is a
TRIPWIRE NET, not a coverage instrument: a PR ships a test only when it touches
an autonomous-operation invariant, and a PR that removes behaviour removes its
tests (`.claude/rules/testing.md`).

## Conventions

- Branch `<type>/<slug>` — `fix/`, `feat/`, `docs/`, `harden/`, `refactor/`.
  Never commit on `main`.
- Conventional commits; the body says WHY. Squash merges.
- The import order in `[tool.importlinter]` (pyproject.toml) is a declared
  fact — `lint-imports` fails a new upward edge.
- Docs honesty: a diff that makes a doc wrong fixes it and its `docs/INDEX.md`
  currency tag in the SAME PR. `docs/INDEX.md` lists every doc.

## Traps

- The test suite runs parallel by default (`-n auto` in pyproject addopts),
  which reads the HOST's core count — `.devclaw/verify` bounds it to the
  sandbox's. Use `-n0` when you need `pdb` or ordered failures.
- Run pytest with a private `TMPDIR`: `/tmp/pytest-of-<user>` can be root-owned
  on this host and crashes every `tmp_path` fixture.
- Durable project knowledge for THIS repo lives in the vault
  (`~/memory/projects/devclaw/`), not in `.devclaw/memory/` — see the
  "Memory (vault)" section of `CLAUDE.md`. A session without vault access
  leaves its facts in the commit message.
