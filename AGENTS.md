# devclaw — AGENTS.md

**What it is:** an MCP server that drives a durable software-development goal
to a merged PR: a session (Claude Code in a docker sandbox) per world change,
the project's CI as the verdict, a read-only review session as the done-gate.
Design: `specs/046-devclaw-v2/spec.md`. Contract: `CLAUDE.md`.

**Build / test / verify**

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) python -m pytest -q     # the stubbed tripwire suite
ruff check . && mypy && lint-imports        # CI gates all three
docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .   # the sandbox image
```

**Layout:** `devclaw/server/` (MCP) → `devclaw/goal/` (the tick rule, the
world, the prompts, the done-gate) → `devclaw/task_queue.py` + `devclaw/queue/`
+ `devclaw/engine/` + `devclaw/gates.py` + `devclaw/delivery/` (one session)
→ `runner/` (inside the sandbox). `docs/architecture.md` is the map;
`docs/INDEX.md` lists every doc with a currency tag.

## Platform first

Use the framework's or platform's standard mechanism before writing a bespoke one, and name it in the PR.
Already in use here, do not reimplement:

- Python: Starlette `FileResponse` for serving the console bundle, no hand-rolled static server.
- Angular (the console): standalone components, signals and zoneless change detection; `provideRouter` with lazy `loadComponent`; `HttpClient` (`provideHttpClient(withFetch())`) for requests; `inject()` for DI; `toSignal` for observables; `poll()` from `@lifekit-hq/core` for refresh; `CmnDialogService` and `ThemeService` from `@lifekit-hq/ui` for dialogs and theme; `@angular/service-worker` with `@lifekit-hq/core/pwa` for the PWA.
