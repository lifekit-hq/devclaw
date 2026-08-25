"""done_when defaults to the referenced issues' acceptance scenarios, read
LIVE at each done-gate round (spec 019 US2, clarified 2026-08-25: evaluation
time — grooming the issue mid-goal steers the finish line). The contract
fetch is LOAD-BEARING: absence or failure blocks the round legibly; the gate
never evaluates against emptiness."""

from __future__ import annotations

import json

import pytest

from devclaw.goal.issue_ref import IssueRefError, IssueSnapshot
from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, FakeIssueFetcher, RecordingNotifier,
    fake_prepare, seed_goal,
)


@pytest.fixture(autouse=True)
def _no_real_deploys(monkeypatch):
    from devclaw.delivery import deploy as deploy_mod

    async def _no_deploy(workspace_dir, slug):
        raise RuntimeError("no deploys under test")

    monkeypatch.setattr(deploy_mod, "deploy_project", _no_deploy)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _tick(store, goal_id, evaluator, engine, notifier, fetcher):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True, issue_fetcher=fetcher,
    )


from devclaw.intake import READY_LABEL


def _snap(n, *, body, state="open", title="t"):
    # ready by default: since US4 the doorway checks readiness before the
    # scenario check, and these tests exercise the scenario semantics.
    return IssueSnapshot(number=n, title=title, body=body, state=state,
                         labels=(READY_LABEL,))


BODY_V1 = "context\n## Acceptance\n- the widget parses the new shape\n## Notes\nx"
BODY_V2 = "context\n## Acceptance\n- the widget parses AND labels the estimate\n## Notes\nx"

ACHIEVED = json.dumps({"verdict": "achieved", "rationale": "scenarios hold",
                       "clauses": [{"clause": "c", "satisfied": True, "evidence": "e"}]})
OFF_TRACK = json.dumps({"verdict": "off_track", "rationale": "not yet",
                        "corrections": ["finish it"]})


def _verifying(store, goal_id="g"):
    store.save_status(goal_id, GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify",
                           is_done_check=True),
    ))


@pytest.mark.asyncio
async def test_defaulted_contract_reads_scenarios_live_at_eval(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    _verifying(store)
    fetcher = FakeIssueFetcher({7: _snap(7, body=BODY_V1)})
    evaluator = FakeClaude(ACHIEVED)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review"))

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)

    assert out is Outcome.DONE
    assert "the widget parses the new shape" in evaluator.last_prompt
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_mid_goal_scenario_edit_honored_next_round(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    _verifying(store)
    fetcher = FakeIssueFetcher({7: _snap(7, body=BODY_V1)})
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review"))

    ev1 = FakeClaude(OFF_TRACK)
    out = await _tick(store, "g", ev1, engine, RecordingNotifier(), fetcher)
    assert out is Outcome.SLEPT
    assert "parses the new shape" in ev1.last_prompt

    # the owner grooms the issue between rounds — the NEXT round judges V2
    fetcher._issues[7] = _snap(7, body=BODY_V2)
    _verifying(store)
    ev2 = FakeClaude(ACHIEVED)
    out = await _tick(store, "g", ev2, engine, RecordingNotifier(), fetcher)
    assert out is Outcome.DONE
    assert "AND labels the estimate" in ev2.last_prompt
    assert "parses the new shape" not in ev2.last_prompt


@pytest.mark.asyncio
async def test_explicit_done_when_overrides_scenarios(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="the /health endpoint returns 200")
    _verifying(store)
    fetcher = FakeIssueFetcher({7: _snap(7, body=BODY_V1)})
    evaluator = FakeClaude(ACHIEVED)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review"))

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)

    assert out is Outcome.DONE
    assert "/health endpoint returns 200" in evaluator.last_prompt
    assert fetcher.calls == 0   # explicit contract: no scenario fetch at all


@pytest.mark.asyncio
async def test_scenario_absence_blocks_round_never_evaluates_empty(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    _verifying(store)
    fetcher = FakeIssueFetcher({7: _snap(7, body="a body with no acceptance section")})
    evaluator = FakeClaude(ACHIEVED)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review"))

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)

    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "needs_answer"
    assert "acceptance" in (s.blocked_on or "").lower()
    assert evaluator.calls == 0          # the gate never ran on emptiness


@pytest.mark.asyncio
async def test_contract_fetch_error_blocks_lost_ref(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    _verifying(store)
    fetcher = FakeIssueFetcher({7: IssueRefError("gh exit 1")})
    evaluator = FakeClaude(ACHIEVED)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review"))

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)

    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.blocked_kind == "lost_ref"
    assert evaluator.calls == 0


# ---- the doorway half (US2 sc.4) -------------------------------------------


def _service(tmp_path):
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    return GoalService(TaskQueue(db), db, config=cfg), db


_KW = dict(
    objective="Fix the referenced issue end to end.",
    workspace_dir="/repos/demo", repo_url="https://github.com/o/r",
    backlog=[], mode="one_shot", out_of_scope=[], invariants=[], established=[],
)


@pytest.mark.asyncio
async def test_creation_refuses_default_when_section_missing(tmp_path):
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _snap(7, body="no section")})
        with pytest.raises(ValueError) as exc:
            await svc.create_goal_async("g", issues=[7], done_when="", **_KW)
        msg = str(exc.value)
        assert "acceptance section" in msg and "explicit done_when" in msg
        with pytest.raises(KeyError):
            svc.get_goal("g")   # nothing persisted
    finally:
        db.close()


@pytest.mark.asyncio
async def test_creation_accepts_default_when_sections_present(tmp_path):
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _snap(7, body=BODY_V1)})
        await svc.create_goal_async("g", issues=[7], done_when="", **_KW)
        g = svc.get_goal("g")
        assert g["issue_refs"] == [7]
        assert g["done_when"] == ""   # the contract stays a POINTER — read live at the gate
    finally:
        db.close()


@pytest.mark.asyncio
async def test_creation_with_explicit_done_when_skips_the_scenario_check(tmp_path):
    svc, db = _service(tmp_path)
    try:
        fetcher = FakeIssueFetcher({7: _snap(7, body="no section")})
        svc._issue_fetcher = fetcher
        await svc.create_goal_async(
            "g", issues=[7], done_when="the endpoint returns 200", **_KW)
        # US4's readiness read still fetched once, but the section-less body
        # did NOT refuse — the scenario check only guards the DEFAULTED path.
        assert fetcher.calls == 1
    finally:
        db.close()
