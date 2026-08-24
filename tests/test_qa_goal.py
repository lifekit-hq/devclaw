"""Spec 015 US3 — the qa goal mode + companion-first triggering: zero-cognition
idle, deploy-triggered runs, the OFF-by-default cadence, hold exclusion, and
the read-only prod smoke."""

from __future__ import annotations

import asyncio

import pytest

from devclaw import issue_doorway as dw
from devclaw import validation_loop as vl
from devclaw.goal import project_hold as _hold
from devclaw.goal import service as service_mod
from devclaw.goal.engine import InFlight, PollResult
from devclaw.goal.models import Goal, GoalStatus, QA_DONE_WHEN, is_standing
from devclaw.goal.service import GoalConfig, GoalService
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

from goal_fakes import Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal


def _store(tmp_path, clock=None):
    return GoalStore(tmp_path, now=clock or Clock())


async def _tick(store, goal_id, evaluator, engine, notifier):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True,
    )


def _seed_qa(tmp_path, goal_id="qa-demo", *, cadence="", project_id="proj"):
    seed_goal(
        tmp_path, goal_id, cadence=cadence, mode="qa",
        done_when=QA_DONE_WHEN, project_id=project_id,
        workspace_dir=f"/repos/{goal_id}",
    )


# ---- zero-token idle (SC-003) ------------------------------------------------

@pytest.mark.asyncio
async def test_idle_qa_tick_costs_zero_cognition_and_dispatches_nothing(tmp_path):
    store = _store(tmp_path)
    _seed_qa(tmp_path)  # cadence "" — the shipped default: disarmed
    evaluator = FakeClaude()
    engine = FakeEngine()

    out = await _tick(store, "qa-demo", evaluator, engine, RecordingNotifier())

    assert out is Outcome.IDLE
    assert evaluator.calls == 0  # the load-bearing zero-token guard
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_disarmed_qa_goal_never_self_initiates_even_long_idle(tmp_path):
    store = _store(tmp_path)
    _seed_qa(tmp_path)
    store.save_status("qa-demo", GoalStatus(
        phase="idle", last_plan_at="2020-01-01T00:00:00+00:00",
    ))
    evaluator = FakeClaude()
    engine = FakeEngine()
    out = await _tick(store, "qa-demo", evaluator, engine, RecordingNotifier())
    assert out is Outcome.IDLE
    assert evaluator.calls == 0 and engine.dispatched == []


# ---- the owner-armed cadence (FR-008) ---------------------------------------

@pytest.mark.asyncio
async def test_armed_cadence_dispatches_one_validation_run(tmp_path):
    store = _store(tmp_path)
    _seed_qa(tmp_path, cadence="1h")
    evaluator = FakeClaude()
    engine = FakeEngine()

    await _tick(store, "qa-demo", evaluator, engine, RecordingNotifier())

    assert evaluator.calls == 0  # dispatch is mechanical — no cognition
    ((action, goal, _url),) = engine.dispatched
    assert action.tool == "validate_product"
    assert action.open_pr is False and action.verify_cmd is None
    assert goal.mode == "qa"


# ---- settled runs: run record, never the done-gate ---------------------------

@pytest.mark.asyncio
async def test_settled_validation_run_logs_record_and_never_opens_done_gate(tmp_path):
    store = _store(tmp_path)
    _seed_qa(tmp_path)
    store.save_status("qa-demo", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "validate_product", "t1", "task", "validate"),
    ))
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="validation: green (5 executed)",
    ))

    await _tick(store, "qa-demo", evaluator, engine, RecordingNotifier())

    assert evaluator.calls == 0  # the done-gate never ran
    assert not any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)
    assert "qa run settled" in store.recent_log("qa-demo")


# ---- single-writer hold exclusion --------------------------------------------

def test_qa_goal_contends_for_nothing():
    qa = Goal(id="qa-x", objective="validate", cadence="", engine="devclaw",
              workspace_dir="/repos/p", mode="qa", project_id="proj",
              done_when=QA_DONE_WHEN)
    assert _hold.scope_key(qa) is None


def test_holder_map_never_elects_a_qa_goal(tmp_path):
    store = _store(tmp_path)
    _seed_qa(tmp_path, "qa-demo", project_id="proj")
    seed_goal(tmp_path, "feature", workspace_dir="/repos/qa-demo", project_id="proj")
    holders = _hold.holder_map(store)
    assert "qa-demo" not in holders.values()
    assert holders.get("proj") == "feature"


# ---- GoalService: qa creation defaults + the deploy trigger ------------------

@pytest.fixture
def svc(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "t.db"))
    queue = TaskQueue(store)
    cfg = GoalConfig(goals_dir=tmp_path / "goals", notify_url="",
                     tick_seconds=900, verify_done=False)
    s = GoalService(queue, store, cfg)
    monkeypatch.setattr(service_mod, "prepare_workspace", fake_prepare)
    yield s, store
    store.close()


def _create_qa(s, tmp_path, goal_id="qa-proj"):
    return s.create_goal(
        goal_id, objective="continuous live validation of the product",
        workspace_dir=str(tmp_path / "ws"), mode="qa", project_id="proj",
        repo_url="https://example.com/product.git",
    )


def test_qa_goal_creation_supplies_standing_contract_and_ships_cadence_off(svc, tmp_path):
    s, _ = svc
    _create_qa(s, tmp_path)
    g = s._goal_store.load_goal("qa-proj")
    assert g.mode == "qa"
    assert g.done_when == QA_DONE_WHEN and is_standing(g.done_when)
    assert (g.cadence or "") == ""  # the periodic schedule SHIPS OFF


async def test_deploy_trigger_enqueues_exactly_one_run_for_opted_in_project(svc, tmp_path):
    s, store = svc
    _create_qa(s, tmp_path)

    gid = await s.trigger_validation("proj")
    assert gid == "qa-proj"
    tasks = [store.get_task(t.id) for t in store.list_tasks()]
    vp = [t for t in tasks if t.kind == "validate_product"]
    assert len(vp) == 1
    assert vp[0].parent_goal_id == "qa-proj"
    assert "triggering validation run" in s._goal_store.recent_log("qa-proj")

    # a second deploy while the run is still in flight does not stack a second run
    gid2 = await s.trigger_validation("proj")
    assert gid2 == "qa-proj"
    tasks = [store.get_task(t.id) for t in store.list_tasks()]
    assert len([t for t in tasks if t.kind == "validate_product"]) == 1
    assert "not stacking" in s._goal_store.recent_log("qa-proj")


async def test_deploy_of_repo_with_no_qa_goal_triggers_nothing(svc, tmp_path):
    s, store = svc
    assert await s.trigger_validation("proj") is None
    assert store.list_tasks() == []


# ---- the read-only prod smoke (FR-009, SC-004) ------------------------------

class FakeGh:
    def __init__(self):
        self.created: list[dict] = []
        self._next = 300

    async def ensure_label(self, repo, name):
        pass

    async def create_issue(self, repo, *, title, body, labels):
        self._next += 1
        self.created.append({"repo": repo, "title": title, "body": body,
                             "labels": labels, "number": self._next})
        return self._next

    async def comment_issue(self, repo, number, *, body):
        return True

    async def reopen_issue(self, repo, number, *, comment):
        return True


def test_smoke_failure_files_deploy_smoke_finding(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "t.db"))
    gh = FakeGh()
    monkeypatch.setattr(dw, "GhCli", lambda: gh)
    monkeypatch.setattr(vl, "prod_smoke", lambda url, path: f"GET {url}{path} returned HTTP 502")

    reason = asyncio.run(vl.run_prod_smoke(
        store, slug="o/r", base_url="http://127.0.0.1:8201", smoke_path="/health",
    ))
    assert reason and "502" in reason
    (created,) = gh.created
    parsed, _ = dw.parse_machine_issue(created["body"], created["title"])
    assert parsed.source == "deploy_smoke"
    assert parsed.fingerprint == "deploy_smoke|o/r|/health"
    assert parsed.severity == "critical"
    store.close()


def test_smoke_ok_files_nothing(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "t.db"))
    gh = FakeGh()
    monkeypatch.setattr(dw, "GhCli", lambda: gh)
    monkeypatch.setattr(vl, "prod_smoke", lambda url, path: None)
    assert asyncio.run(vl.run_prod_smoke(
        store, slug="o/r", base_url="http://127.0.0.1:8201",
    )) is None
    assert gh.created == []
    store.close()
