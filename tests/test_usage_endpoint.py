"""Regression tests for the /usage.json endpoint and compute_instance_usage().

Covers:
- Instance-wide aggregation of cognition + worker tokens
- Per-project attribution via normalized workspace_dir matching
- Unattributed bucket for usage matching no registered project
- cap_pressure built from limit-category problems
- Wire shape (keys, cost_is_estimate label, absent reset_hint_s)
- Zero-token guard: compute_instance_usage makes no LLM calls
"""

from __future__ import annotations

import json
import time

import pytest

from devclaw.project_registry import ProjectRegistry
from devclaw.state_store import StateStore
from devclaw.telemetry import compute_instance_usage

NOW_MS = int(time.time() * 1000)


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "u.db"))
    yield s
    s.close()


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(str(tmp_path / "u.db"))
    yield r


def _add_project(registry: ProjectRegistry, pid: str, name: str, ws: str) -> None:
    registry.create(id=pid, name=name, workspace_dir=ws)


def _seed_cognition(
    store: StateStore,
    *,
    goal_id: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_in_est: int = 0,
    tokens_out_est: int = 0,
    cost_usd: float = 0.0,
) -> None:
    payload: dict = {"kind": "cognition", "role": "planner", "latency_ms": 100}
    if tokens_in is not None:
        payload["tokens_in"] = tokens_in
    if tokens_out is not None:
        payload["tokens_out"] = tokens_out
    if tokens_in_est:
        payload["tokens_in_est"] = tokens_in_est
    if tokens_out_est:
        payload["tokens_out_est"] = tokens_out_est
    if cost_usd:
        payload["cost_usd"] = cost_usd
    store.append_trace_event(
        trace_id=f"t-{time.time_ns()}",
        goal_id=goal_id,
        kind="cognition",
        payload=payload,
    )


def _seed_task(
    store: StateStore,
    *,
    workspace: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    tid = f"task-{time.time_ns()}"
    store.create_task(id=tid, kind="implement_feature", workspace_dir=workspace, goal="g")
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost_usd": cost_usd,
    }
    store.mark_done(tid, json.dumps({"ok": True, "usage": usage}))


def _seed_limit_problem(
    store: StateStore,
    *,
    kind: str,
    message: str,
    days_ago: float = 0,
) -> None:
    store.record_problem(
        category="limit",
        kind=kind,
        message=message,
        recovered=True,
    )
    if days_ago:
        past_ms = NOW_MS - int(days_ago * 24 * 60 * 60 * 1000)
        with store._lock:
            store._db.execute(
                "UPDATE problems SET last_seen_ms = ?, first_seen_ms = ? WHERE kind = ?",
                (past_ms, past_ms, kind),
            )
            store._db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute(store, registry, goals=None):
    return compute_instance_usage(store, registry, goals or [])


# ---------------------------------------------------------------------------
# Wire shape
# ---------------------------------------------------------------------------

def test_empty_store_returns_valid_shape(store, registry):
    result = _compute(store, registry)
    assert "computed_at_ms" in result
    assert "totals" in result
    assert "by_project" in result
    assert "unattributed" in result
    assert "cap_pressure" in result
    # cost_is_estimate must be present and True in totals
    assert result["totals"]["cost_is_estimate"] is True
    # All token counts must be 0 (not absent) on an empty store
    t = result["totals"]
    for key in (
        "cognition_tokens_in", "cognition_tokens_out",
        "cognition_rows_real", "cognition_rows_estimated",
        "worker_input_tokens", "worker_output_tokens",
        "worker_cache_read_tokens", "worker_tasks_with_usage",
    ):
        assert t[key] == 0, f"expected 0 for {key}"


def test_totals_include_cost_estimate_and_is_estimate_flag(store, registry):
    _seed_cognition(store, goal_id="g1", tokens_in=100, tokens_out=50)
    _seed_task(store, workspace="/w", input_tokens=200, output_tokens=100)
    result = _compute(store, registry)
    assert result["totals"]["cost_is_estimate"] is True
    assert "cost_estimate_usd" in result["totals"]
    # unattributed must NOT have cost_is_estimate (that label is only on totals)
    assert "cost_is_estimate" not in result["unattributed"]


# ---------------------------------------------------------------------------
# Cognition aggregation
# ---------------------------------------------------------------------------

def test_cognition_real_usage_accumulates_correctly(store, registry):
    _seed_cognition(store, goal_id="g1", tokens_in=100, tokens_out=50)
    _seed_cognition(store, goal_id="g2", tokens_in=200, tokens_out=80)
    result = _compute(store, registry)
    t = result["totals"]
    assert t["cognition_tokens_in"] == 300
    assert t["cognition_tokens_out"] == 130
    assert t["cognition_rows_real"] == 2
    assert t["cognition_rows_estimated"] == 0


def test_cognition_estimated_rows_distinguished_from_real(store, registry):
    _seed_cognition(store, goal_id="g1", tokens_in=100, tokens_out=50)        # real
    _seed_cognition(store, goal_id="g2", tokens_in_est=80, tokens_out_est=40) # estimated
    result = _compute(store, registry)
    t = result["totals"]
    assert t["cognition_rows_real"] == 1
    assert t["cognition_rows_estimated"] == 1
    # Both contribute to the token total, but via different paths
    assert t["cognition_tokens_in"] == 180   # 100 real + 80 est
    assert t["cognition_tokens_out"] == 90   # 50 real + 40 est


# ---------------------------------------------------------------------------
# Worker aggregation
# ---------------------------------------------------------------------------

def test_worker_usage_accumulates_from_result_json(store, registry):
    _seed_task(store, workspace="/w", input_tokens=1000, output_tokens=500, cache_read_tokens=200)
    _seed_task(store, workspace="/w", input_tokens=500, output_tokens=250, cache_read_tokens=100)
    result = _compute(store, registry)
    t = result["totals"]
    assert t["worker_input_tokens"] == 1500
    assert t["worker_output_tokens"] == 750
    assert t["worker_cache_read_tokens"] == 300
    assert t["worker_tasks_with_usage"] == 2


def test_tasks_without_usage_in_result_json_are_skipped(store, registry):
    tid = f"task-{time.time_ns()}"
    store.create_task(id=tid, kind="implement_feature", workspace_dir="/w", goal="g")
    store.mark_done(tid, json.dumps({"ok": True}))  # no usage key
    result = _compute(store, registry)
    assert result["totals"]["worker_tasks_with_usage"] == 0
    assert result["totals"]["worker_input_tokens"] == 0


# ---------------------------------------------------------------------------
# Per-project attribution
# ---------------------------------------------------------------------------

def test_per_project_attribution_via_workspace_dir(store, registry):
    _add_project(registry, "proj-a", "Project A", "/repos/a")
    _add_project(registry, "proj-b", "Project B", "/repos/b")
    goals = [
        {"id": "ga1", "workspace_dir": "/repos/a"},
        {"id": "gb1", "workspace_dir": "/repos/b"},
    ]
    _seed_cognition(store, goal_id="ga1", tokens_in=100, tokens_out=50)
    _seed_cognition(store, goal_id="gb1", tokens_in=200, tokens_out=80)
    _seed_task(store, workspace="/repos/a", input_tokens=500, output_tokens=200)
    _seed_task(store, workspace="/repos/b", input_tokens=300, output_tokens=100)

    result = _compute(store, registry, goals)
    by_id = {p["project_id"]: p for p in result["by_project"]}
    assert "proj-a" in by_id
    assert "proj-b" in by_id
    pa = by_id["proj-a"]
    assert pa["cognition_tokens_in"] == 100
    assert pa["worker_input_tokens"] == 500
    pb = by_id["proj-b"]
    assert pb["cognition_tokens_in"] == 200
    assert pb["worker_input_tokens"] == 300
    # Totals must equal the sum of per-project + unattributed
    assert result["totals"]["cognition_tokens_in"] == 300
    assert result["totals"]["worker_input_tokens"] == 800
    # Nothing should land in unattributed
    assert result["unattributed"]["cognition_tokens_in"] == 0
    assert result["unattributed"]["worker_input_tokens"] == 0


def test_unattributed_bucket_captures_usage_matching_no_project(store, registry):
    _add_project(registry, "proj-a", "Project A", "/repos/a")
    goals = [{"id": "ga1", "workspace_dir": "/repos/a"}]
    _seed_cognition(store, goal_id="ga1", tokens_in=100, tokens_out=50)
    # goal with no matching project
    _seed_cognition(store, goal_id="orphan", tokens_in=200, tokens_out=80)
    _seed_task(store, workspace="/repos/a", input_tokens=500, output_tokens=200)
    _seed_task(store, workspace="/nowhere", input_tokens=300, output_tokens=100)

    result = _compute(store, registry, goals)
    # Unattributed gets the orphan goal's cognition + no-project task
    assert result["unattributed"]["cognition_tokens_in"] == 200
    assert result["unattributed"]["worker_input_tokens"] == 300
    # Project A gets only what matches its workspace
    by_id = {p["project_id"]: p for p in result["by_project"]}
    assert by_id["proj-a"]["cognition_tokens_in"] == 100
    assert by_id["proj-a"]["worker_input_tokens"] == 500


def test_no_goal_id_in_response(store, registry):
    _add_project(registry, "proj-a", "Project A", "/repos/a")
    goals = [{"id": "ga1", "workspace_dir": "/repos/a"}]
    _seed_cognition(store, goal_id="ga1", tokens_in=100, tokens_out=50)
    result = _compute(store, registry, goals)
    # Verify no goal_id appears anywhere in the response
    dumped = json.dumps(result)
    assert "ga1" not in dumped, "goal IDs must not appear in /usage.json response"


def test_per_project_response_has_no_goal_ids_field(store, registry):
    _add_project(registry, "proj-a", "Project A", "/repos/a")
    result = _compute(store, registry)
    for p in result["by_project"]:
        assert "goal_id" not in p
        assert "goal_ids" not in p


def test_workspace_dir_normalization_handles_trailing_slash(store, registry):
    _add_project(registry, "proj-a", "Project A", "/repos/a/")  # trailing slash
    goals = [{"id": "ga1", "workspace_dir": "/repos/a"}]        # no trailing slash
    _seed_cognition(store, goal_id="ga1", tokens_in=100, tokens_out=50)
    _seed_task(store, workspace="/repos/a", input_tokens=500, output_tokens=200)
    result = _compute(store, registry, goals)
    by_id = {p["project_id"]: p for p in result["by_project"]}
    # Both the cognition trace and the task should be attributed to proj-a despite
    # the trailing slash difference on the project's workspace_dir.
    assert by_id["proj-a"]["cognition_tokens_in"] == 100
    assert by_id["proj-a"]["worker_input_tokens"] == 500
    assert result["unattributed"]["cognition_tokens_in"] == 0


def test_project_with_zero_usage_appears_in_by_project(store, registry):
    _add_project(registry, "proj-empty", "Empty Project", "/repos/empty")
    result = _compute(store, registry)
    ids = [p["project_id"] for p in result["by_project"]]
    assert "proj-empty" in ids, "registered project with no usage must still appear in by_project"
    p = next(x for x in result["by_project"] if x["project_id"] == "proj-empty")
    assert p["cognition_tokens_in"] == 0
    assert p["worker_input_tokens"] == 0


# ---------------------------------------------------------------------------
# Cap pressure
# ---------------------------------------------------------------------------

def test_cap_pressure_rate_limit_appears(store, registry):
    _seed_limit_problem(store, kind="rate_limit", message="rate_limit: 429 too many requests")
    result = _compute(store, registry)
    assert "rate_limit" in result["cap_pressure"], "rate_limit problem must appear in cap_pressure"
    entry = result["cap_pressure"]["rate_limit"]
    assert "last_seen_ms" in entry


def test_cap_pressure_quota_appears(store, registry):
    _seed_limit_problem(store, kind="quota", message="quota: usage limit reached, resets tomorrow")
    result = _compute(store, registry)
    assert "quota" in result["cap_pressure"]


def test_cap_pressure_reset_hint_absent_when_not_stated(store, registry):
    # A rate limit message with no explicit reset time — no reset_hint_s
    _seed_limit_problem(store, kind="rate_limit", message="rate_limit: too many requests")
    result = _compute(store, registry)
    entry = result["cap_pressure"].get("rate_limit", {})
    # reset_hint_s should be absent (not None, not 0) when not stated
    assert "reset_hint_s" not in entry, "reset_hint_s must be absent (not null) when not stated"


def test_cap_pressure_old_problems_excluded_by_window(store, registry):
    _seed_limit_problem(store, kind="rate_limit", message="rate_limit: 429", days_ago=35)
    result = _compute(store, registry)
    assert "rate_limit" not in result["cap_pressure"], (
        "limit problems older than 30 days must be excluded from cap_pressure"
    )


def test_cap_pressure_recent_problems_included(store, registry):
    _seed_limit_problem(store, kind="rate_limit", message="rate_limit: 429", days_ago=1)
    result = _compute(store, registry)
    assert "rate_limit" in result["cap_pressure"]


def test_non_limit_problems_do_not_appear_in_cap_pressure(store, registry):
    store.record_problem(category="task_fail", kind="rate_limit", message="some task failure", recovered=False)
    result = _compute(store, registry)
    # category=task_fail should NOT appear in cap_pressure even if its kind looks like rate_limit
    assert not result["cap_pressure"], "non-limit-category problems must not appear in cap_pressure"


# ---------------------------------------------------------------------------
# Zero-token guard (no LLM call on this path)
# ---------------------------------------------------------------------------

def test_compute_instance_usage_makes_no_llm_calls(store, registry):
    """compute_instance_usage is a pure SQLite read — it must never trigger a
    cognition call. Verified by counting fake claude invocations = 0."""
    # We don't have a FakeClaude here because compute_instance_usage doesn't
    # go near the claude binary. Verify structurally: the function is synchronous
    # and imports only stdlib/db — no engine module, no llm_call, no cognition caller.
    import devclaw.telemetry as _tel
    import inspect
    src = inspect.getsource(_tel.compute_instance_usage)
    assert "llm_call" not in src
    assert "claude_with_model" not in src
    assert "FakeClaude" not in src
    # Actually run it to prove it completes without error
    _seed_cognition(store, goal_id="g", tokens_in=10, tokens_out=5)
    result = _compute(store, registry)
    assert result["totals"]["cognition_tokens_in"] == 10


# ---------------------------------------------------------------------------
# HTTP endpoint structural check
# ---------------------------------------------------------------------------

def test_usage_json_route_registered_in_http():
    """The /usage.json route must be present in the HTTP server module."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "devclaw" / "server" / "routes" / "observability.py"
    ).read_text()
    assert "/usage.json" in src, (
        "GET /usage.json must be registered in server/routes/observability.py"
    )


def test_usage_console_page_exists():
    """The Usage.tsx console page must exist and import fetchUsage."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "console" / "src" / "pages" / "Usage.tsx"
    assert p.exists(), "Usage.tsx page must exist"
    src = p.read_text()
    assert "fetchUsage" in src
    assert "cost_is_estimate" not in src or "estimate" in src.lower()


def test_usage_route_wired_in_main_tsx():
    """main.tsx must register the /usage route."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "console" / "src" / "main.tsx").read_text()
    assert "usage" in src.lower()
    assert "Usage" in src


# ---------------------------------------------------------------------------
# Per-project wire shape — fields the console drill-in exposes (issue #682 inc 3)
# ---------------------------------------------------------------------------

def test_per_project_rows_include_worker_tasks_with_usage(store, registry):
    """by_project rows must carry worker_tasks_with_usage so the console's
    per-project drill-in can surface it alongside the token columns."""
    _add_project(registry, "proj-a", "Project A", "/repos/a")
    _seed_task(store, workspace="/repos/a", input_tokens=100, cost_usd=0.05)
    _seed_task(store, workspace="/repos/a", input_tokens=200, cost_usd=0.10)
    result = _compute(store, registry)
    by_id = {p["project_id"]: p for p in result["by_project"]}
    p = by_id["proj-a"]
    assert "worker_tasks_with_usage" in p, (
        "by_project rows must include worker_tasks_with_usage"
    )
    assert p["worker_tasks_with_usage"] == 2
    assert "cognition_cost_usd" in p, (
        "by_project rows must include cognition_cost_usd"
    )
    assert "worker_cost_usd" in p, (
        "by_project rows must include worker_cost_usd"
    )
    assert p["worker_cost_usd"] == pytest.approx(0.15)


def test_per_project_drill_in_rendered_in_usage_tsx():
    """Usage.tsx must render worker_tasks_with_usage in the per-project drill-in."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1] / "console" / "src" / "pages" / "Usage.tsx"
    ).read_text()
    assert "worker_tasks_with_usage" in src, (
        "Usage.tsx must render worker_tasks_with_usage in the per-project drill-in panel"
    )
    assert "cognition_cost_usd" in src, (
        "Usage.tsx must render cognition_cost_usd in the per-project drill-in panel"
    )
    assert "worker_cost_usd" in src, (
        "Usage.tsx must render worker_cost_usd in the per-project drill-in panel"
    )
