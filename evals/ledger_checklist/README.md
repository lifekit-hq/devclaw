# `evals/ledger_checklist/` — the hidden grader for the compounding experiment

Part of the **compounding experiment** ([`docs/proposals/compounding-experiment.md`](../../docs/proposals/compounding-experiment.md),
P1 LOCKED). This directory is the **hidden acceptance checklist** for the "Ledger"
target app — the grader that answers, mechanically, *which features actually
work* against the target repo at a given moment.

It lives in the **devclaw repo**, never in the target repo. The worker only ever
sees the prose [`SPEC.md`](./SPEC.md); these executable checks are how a night's
progress is graded. That separation is deliberate — devclaw's own
**trust-the-input / verify-the-output** principle — so a "green" is earned by the
feature working, not by satisfying a check the worker could read and game (#358).

## Files

| File | What it is | Who reads it |
|---|---|---|
| `SPEC.md` | The prose brief — *what to build*. Placed in the target repo + mirrored into the goal's `done_when` at experiment start. | the **worker** |
| `checklist.py` | The 10 executable checks (`c1`..`c10`) + the fail-closed `run_checklist` runner + `criteria_vector`. | the **scorecard** (P1-C) |
| `README.md` | This file. | humans |

## The two layers (why the tests pass off the box)

- **Probes** (`_c1`..`_c10`) shell out to `dotnet` / `ng` / an HTTP client /
  Playwright. They only *execute* meaningfully on the live box (real toolchain +
  a booted app). Best-effort real; **live-validated**.
- **Runner** (`run_checklist`) is pure orchestration over an injected `CheckCtx`.
  Its behaviour — full-vector production, and failing a crashing probe **closed**
  (never a green, never an escaping exception) — is locked in
  `tests/test_ledger_checklist_runner.py` with a fake ctx. No toolchain needed,
  same stub discipline as the rest of the suite.

## The live-box execution contract (what P1-C's runner does)

The compounding scorecard (P1-C, next PR) is what constructs a **real**
`CheckCtx` and calls `run_checklist`. Per night it must:

1. **Clone** the target repo at `HEAD` into a scratch dir (`ctx.repo`).
2. **Boot** the backend (`dotnet run --project backend`) on an ephemeral port;
   build + serve the frontend as needed for `c8`–`c10`. Tear both down after.
3. Wire the ctx:
   - `ctx.sh(cmd, cwd=None)` → run `cmd` in `repo/<cwd>`, return `ShResult(rc, out)`.
   - `ctx.api(method, path, token=None, json=None)` → call the booted backend's
     base URL, return `ApiResult(status, body)`. `token="valid"` should present a
     real logged-in token; `token=None` presents none (for the 401 check).
4. `run_checklist(ctx)` → `{id: CheckResult}` → `criteria_vector(...)` → the
   night's N-bit snapshot, appended to the `compounding_runs` projection.

Everything above is **live-box only** — it needs a .NET/Node toolchain and a
running app, which the stubbed pytest suite deliberately never has. The scorecard
that orchestrates it is stub-tested at the mechanics level (scripted vectors),
mirroring how `measure_passrate` / `measure_goal_loop` are structured.

## Judged-not-executed fallback (`c9`/`c10`)

The Playwright-backed UI checks can be flaky in a way a build/endpoint check is
not. When a smoke can't produce a clean verdict, the live runner may mark that
one criterion `CheckResult(mode="judged")` (the grounded evaluator's call),
**logged explicitly** — never silently promoted to a pass. Executed verdicts are
always preferred; a judged verdict is visible as such in the scorecard.
