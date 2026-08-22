# AGENTS.md — devclaw engineering harness

<!-- devclaw:managed:start -->
> **Map:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) — where each component lives, how a
> route is added, how a task flows from dispatch to gate, and which writer owns each
> SQLite table. Read it before exploring the tree.
> **Contract:** [`CLAUDE.md`](./CLAUDE.md) (working) and
> [`docs/architecture.md`](./docs/architecture.md) (locked invariants).
> **In-flight specs:** [`.specify/specs/`](./.specify/specs/).


## Stack

- **Python** ≥3.11; package root `devclaw/`; sandbox runner at `runner/runner.py`
- **Database**: SQLite (`devclaw.db`), single-writer/lock discipline; `StateStore` owns all mutations
- **MCP surface**: FastMCP via `devclaw/server/http.py` + `devclaw/server/tools.py`
- **Tests**: pytest (fully stubbed, no docker, no claude) — ~1900 tests in ~190s

## Build / test

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) python -m pytest -q   # private tmpdir avoids root-owned basetemp
```

Always use a private TMPDIR — `/tmp/pytest-of-<user>` can be root-owned on this host, crashing `tmp_path` fixtures.

## Layout

| Path | Purpose |
|------|---------|
| `devclaw/server/http.py` | HTTP routes + MCP surface (dashboard, `/problems.json`, etc.) |
| `devclaw/server/tools.py` | MCP tool definitions |
| `devclaw/state_store/` | All DB writes; `ProblemsMixin`, `GoalStore`, `StateStore` |
| `devclaw/goal/` | Goal state machine, tick, planner, evaluator |
| `devclaw/engine/` | Sandbox container launcher |
| `runner/runner.py` | In-sandbox agent harness (layer 5) |
| `runner/skills/` | Skill markdown files (synced to `/opt/devclaw/skills/`) |
| `tests/` | Pytest suite — all stubbed |

## Key conventions

- `/opt/devclaw/skills/_common.md` is the installed copy of `runner/skills/_common.md` — keep them in sync when editing either.
- `store.list_problems(since_ms=...)` accepts an epoch-ms lower bound; `None` = no filter.
- `/problems.json` defaults to a 30-day lookback; `?since_ms=0` bypasses it for all-time.
- OAuth only — `ANTHROPIC_API_KEY` is actively stripped; never add an API-key code path.
- Zero-token idle guard: no LLM calls on idle/blocked tick paths (`FakeClaude.calls == 0` tests are load-bearing).
- Single writer: only `TaskQueue` mutates task rows; `StateStore` is append-only.

## Gotchas

- In a git worktree, verify `python -c "import devclaw; print(devclaw.__file__)"` points to the WORKTREE, not the main checkout.
- `tests/test_runner_wrappers.py` tests the in-sandbox runner (`runner/runner.py`) by loading it directly — it runs against `/opt/devclaw/skills/`, not the source copy.
<!-- devclaw:managed:end -->
