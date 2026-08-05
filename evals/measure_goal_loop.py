"""Thin-plan GOAL-loop smoke — a real long_lived goal driven end-to-end through
the REAL engine with the planner CUT (``DEVCLAW_THIN_PLAN=1``).

Where ``measure_passrate`` dispatches a single hand-written ticket (and so never
exercises the goal planner at all), this drives ``GoalService.tick_one`` on a
real ``long_lived`` goal so the THIN advance path (``_handle_long_lived_advance``)
runs for real: each tick mechanically dispatches an "advance the goal / maintain
PLAN.md" worker session (ZERO per-tick planner cognition), and the grounded
done-gate — the ONE surviving cognition boundary — judges completion against
``done_when``. This is the instrument P3b actually needs: proof that a goal
pursued with no planner still advances and CLOSES autonomously.

Real engine (docker sandbox + claude over OAuth), real done-gate cognition, real
delivery. Env MUST be set before importing devclaw (the sandcastle runner reads
image/model at import). Run:

    DEVCLAW_SANDBOX_IMAGE=devclaw-sandbox:latest DEVCLAW_EXEC_MODEL= \
        .venv/bin/python evals/measure_goal_loop.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

# --- env wiring (BEFORE importing devclaw) ---------------------------------
os.environ.setdefault("DEVCLAW_SANDBOX_IMAGE", "devclaw-sandbox:latest")
os.environ["DEVCLAW_THIN_PLAN"] = "1"  # <<< the whole point: cut the planner
os.environ.setdefault("DEVCLAW_GOAL_PLAIN_SUMMARY", "0")  # skip owner-summary cognition
os.environ.setdefault("DEVCLAW_SELF_TRIAGE", "0")
os.environ.setdefault("DEVCLAW_TREND_ENABLED", "0")
# DEVCLAW_ENGINE unset → real run_sandcastle. Belt: never metered billing.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

GOAL_ID = os.environ.get("LOOP_GOAL_ID", "thin-loop-strkit")
REPO_URL = os.environ.get("LOOP_REPO_URL", "https://github.com/dsdevq/thin-plan-smoke.git")
REPO_SLUG = REPO_URL.split("github.com/", 1)[-1].removesuffix(".git").removesuffix("/")
MAX_TICKS = int(os.environ.get("LOOP_MAX_TICKS", "4"))
WORKROOT = Path(os.environ.get("LOOP_WORKROOT", str(Path.home() / "projects" / ".devclaw-goal-loop")))

OBJECTIVE = os.environ.get("LOOP_OBJECTIVE") or (
    "Build a small, importable Python string-utilities library called `strkit`. "
    "It should provide these functions, each with a clear docstring: "
    "`slugify(text)` (lowercase; runs of spaces/punctuation become single hyphens; trimmed), "
    "`truncate(text, length, suffix='…')` (cut to at most `length` chars, appending `suffix` when it cuts, "
    "never exceeding `length`), `word_count(text)` (count of whitespace-separated words), and "
    "`is_palindrome(text)` (case- and non-alphanumeric-insensitive palindrome check). "
    "Ship it as a proper installable package (pyproject.toml, `strkit` importable), with a pytest test suite "
    "covering normal and edge cases, and a README documenting each function with a usage example."
)
DONE_WHEN = os.environ.get("LOOP_DONE_WHEN") or (
    "The `strkit` package is importable and exposes slugify, truncate, word_count, and is_palindrome; "
    "every function has pytest tests covering normal and edge cases; `python -m pytest` passes with no failures; "
    "and README.md documents each function with a usage example."
)
VERIFY_CMD = os.environ.get("LOOP_VERIFY_CMD") or "pip install -q pytest && pip install -q -e . && python -m pytest -q"


def _seed_goal(goals_dir: Path, workspace_dir: Path) -> None:
    import yaml
    d = goals_dir / GOAL_ID
    d.mkdir(parents=True, exist_ok=True)
    (d / "goal.yaml").write_text(
        yaml.safe_dump(
            {
                "objective": OBJECTIVE,
                "cadence": "1d",
                "engine": "devclaw",
                "workspace_dir": str(workspace_dir),
                "repo_url": REPO_URL,
                "verify_cmd": VERIFY_CMD,
                "open_pr": True,
                "done_when": DONE_WHEN,
                "mode": "long_lived",
            },
            sort_keys=False,
        )
    )


async def _drain_settled(queue, store) -> None:
    """Drain until in-flight work settles, waiting out account-wide pauses
    (usage/rate/auth) exactly like measure_passrate's _settle."""
    for _ in range(240):  # generous bound; each loop is a drain or a pause-wait
        await queue.drain()
        until, reason = store.global_pause()
        now_ms = time.time() * 1000
        if until and until > now_ms:
            wait_s = (until - now_ms) / 1000 + 5
            print(f"    (queue paused: {reason.strip()}; waiting ~{int(wait_s)}s)", flush=True)
            await asyncio.sleep(wait_s)
            continue
        return


def _merge_open_pr() -> str | None:
    """Squash-merge the goal's open PR (best-effort, retried past the GitHub
    flakiness seen this session) so the increment lands on main and the NEXT
    tick's prepare_workspace picks up PLAN.md + the code. Returns the merged PR
    url or None."""
    for attempt in range(4):
        r = subprocess.run(
            ["gh", "pr", "list", "--repo", REPO_SLUG,
             "--state", "open", "--json", "number,url", "--jq", ".[0].number"],
            capture_output=True, text=True,
        )
        num = (r.stdout or "").strip()
        if not num:
            return None
        m = subprocess.run(
            ["gh", "pr", "merge", num, "--repo", REPO_SLUG,
             "--squash", "--admin", "--delete-branch"],
            capture_output=True, text=True,
        )
        if m.returncode == 0:
            print(f"    merged PR #{num} to main", flush=True)
            return num
        print(f"    (merge attempt {attempt + 1} failed: {m.stderr.strip()[-160:]})", flush=True)
        time.sleep(4)
    return None


async def main() -> None:
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue
    from devclaw.engine.sandcastle import run_sandcastle, SANDBOX_IMAGE, EXEC_MODEL
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.goal.store import GoalStore
    from devclaw.goal.models import GoalStatus
    import devclaw.goal.tick as tick_mod

    tick_mod.AUTODEPLOY_ENABLED = False  # no Tailscale deploy on 'achieved'

    if WORKROOT.exists():
        shutil.rmtree(WORKROOT)
    WORKROOT.mkdir(parents=True, exist_ok=True)
    goals_dir = WORKROOT / "goals"
    workspace_dir = WORKROOT / "ws"
    goals_dir.mkdir(parents=True, exist_ok=True)

    print(f"image={SANDBOX_IMAGE} exec_model={EXEC_MODEL} thin_plan=ON goal={GOAL_ID}", flush=True)
    print(f"objective: {OBJECTIVE[:90]}...", flush=True)

    _seed_goal(goals_dir, workspace_dir)
    store = StateStore(str(WORKROOT / "loop.db"))
    gs = GoalStore(goals_dir, state=store)
    gs.save_status(GOAL_ID, GoalStatus(lifecycle="executing"))  # skip firming — thin path owns planning

    queue = TaskQueue(store, runner=run_sandcastle)
    if queue.engine_kind == "stub":
        raise SystemExit("refusing to run: queue resolved the STUB engine, not the real one")
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, eval_every=3, verify_done=True)
    svc = GoalService(queue, store, cfg)  # real cognition callers (done-gate) via defaults

    outcomes: list[str] = []
    try:
        for i in range(MAX_TICKS):
            t0 = time.time()
            print(f"\n=== TICK {i + 1}/{MAX_TICKS} ===", flush=True)
            outcome = await svc.tick_one(GOAL_ID)
            await _drain_settled(queue, store)
            st = gs.load_status(GOAL_ID)
            wall = round(time.time() - t0, 1)
            print(f"  outcome={outcome}  lifecycle={st.lifecycle}  phase={st.phase}  "
                  f"in_flight={st.in_flight is not None}  wall={wall}s", flush=True)
            outcomes.append(str(outcome))

            if str(getattr(outcome, "value", outcome)) in ("done", "skip_done", "skip_cancelled") \
                    or st.phase in ("done", "cancelled"):
                print("  >>> terminal — goal closed by the loop", flush=True)
                break

            # Land the increment on main so the next tick accumulates (PLAN.md + code).
            _merge_open_pr()
    finally:
        await queue.drain()

    final = gs.load_status(GOAL_ID)
    print("\n" + "=" * 60, flush=True)
    print(f"THIN-PLAN GOAL LOOP — final lifecycle={final.lifecycle} phase={final.phase}", flush=True)
    print(f"tick outcomes: {outcomes}", flush=True)
    achieved = final.phase == "done"
    print(f"AUTONOMOUS CLOSE (done-gate reached 'achieved'): {achieved}", flush=True)
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
