# AGENTS.md — devclaw engineering harness

<!-- devclaw:managed:start -->
> **Map:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) — where each component lives, how a
> route is added, how a task flows from dispatch to gate, and which writer owns each
> SQLite table. Read it before exploring the tree.
> **Contract:** [`CLAUDE.md`](./CLAUDE.md) (working) and
> [`docs/architecture.md`](./docs/architecture.md) (locked invariants).
> **In-flight specs:** [`specs/`](./specs/) at the repo root. `.specify/` holds only
> the pipeline's scripts, templates, and the constitution — never a spec.


## Stack

- **Python** ≥3.11; package root `devclaw/`; sandbox runner at `runner/runner.py`
- **Database**: SQLite (`devclaw.db`), single-writer/lock discipline; `StateStore` owns all mutations
- **MCP surface**: FastMCP — tools in `devclaw/server/tools.py`, HTTP routes in `devclaw/server/routes/`
- **Tests**: pytest (fully stubbed, no docker, no claude) — ~1262 tests in ~21s (`-n auto`)
- **Lint**: `ruff check .` — pyflakes + syntax errors only (`select = ["F", "E9"]`)

## Build / test

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) python -m pytest -q   # private tmpdir avoids root-owned basetemp
```

Always use a private TMPDIR — `/tmp/pytest-of-<user>` can be root-owned on this host, crashing `tmp_path` fixtures.

## Layout

| Path | Purpose |
|------|---------|
| `devclaw/server/routes/` | HTTP routes, one module per resource (`goals`, `projects`, `tasks`, `console`, `control`, `observability`, `evals`) |
| `devclaw/server/http.py` | No routes — imports every route module, because registration IS import (#625) |
| `devclaw/server/tools.py` | MCP tool definitions |
| `devclaw/state_store/` | Task/event DB writes; `StateStore` + `ProblemsMixin` |
| `devclaw/goal/store/` | Goal-state DB writes; `GoalStore`, CAS'd through `transition()` |
| `devclaw/goal/` | Goal state machine, tick, done-gate evaluator |
| `devclaw/engine/` | Sandbox container launcher |
| `runner/runner.py` | In-sandbox agent harness (layer 5) |
| `runner/skills/` | Skill markdown — the ONE source, baked into the image at `/opt/devclaw/skills/` |
| `tests/` | Pytest suite — all stubbed |

## Key conventions

- `runner/skills/` is the ONE home for worker-kind instructions — baked to `/opt/devclaw/skills/` in the sandbox image, pointed at in-repo by the host engine (`DEVCLAW_SKILLS_DIR`). Never edit an installed copy and never add a second one: a fallback copy is a silent fork (#610). A missing bundle fails loud (`skills_missing`), never substitutes text (#613).
- `store.list_problems(since_ms=...)` accepts an epoch-ms lower bound; `None` = no filter.
- `/problems.json` defaults to a 30-day lookback; `?since_ms=0` bypasses it for all-time.
- OAuth only — `ANTHROPIC_API_KEY` is actively stripped; never add an API-key code path.
- Zero-token idle guard: no LLM calls on idle/blocked tick paths (`FakeClaude.calls == 0` tests are load-bearing).
- Single writer: only `TaskQueue` mutates task rows; `StateStore` is append-only.

## Gotchas

- In a git worktree, verify `python -c "import devclaw; print(devclaw.__file__)"` points to the WORKTREE, not the main checkout.
- Runner tests load `runner/runner.py` from the SOURCE tree via `importlib` (it's a script, not a package) and exercise pure functions. `tests/test_runner_skills.py` points `_SKILLS_DIR` at in-repo `runner/skills/` — no test depends on an installed `/opt/devclaw/skills/`.
<!-- devclaw:managed:end -->
