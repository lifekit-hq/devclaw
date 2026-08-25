"""The goal-as-pointer doorway (spec 019 US1) — structural validation of
first-class issue references, hard refusals with actionable messages, and the
issue-less lane's byte-compatibility."""

from __future__ import annotations

import pytest

from devclaw.goal.issue_ref import validate_refs
from devclaw.goal.store import GoalStore
from tests.goal_fakes import Clock, seed_goal


# ---- validate_refs: every refusal names rule + input + fixing verb ---------


def test_refs_require_a_repository_naming_the_issue_less_lane():
    with pytest.raises(ValueError) as exc:
        validate_refs([1], repo_url=None)
    msg = str(exc.value)
    assert "repository" in msg and "issue-less lane" in msg


def test_non_positive_and_non_int_refs_refused():
    for bad in ([0], [-3], ["7"], [True]):
        with pytest.raises(ValueError) as exc:
            validate_refs(bad, repo_url="https://github.com/o/r")
        assert "positive issue number" in str(exc.value)


def test_duplicate_refs_refused_naming_the_duplicate():
    with pytest.raises(ValueError) as exc:
        validate_refs([4, 7, 4], repo_url="https://github.com/o/r")
    assert "#4" in str(exc.value) and "twice" in str(exc.value)


def test_empty_and_none_pass_through_as_the_issue_less_lane():
    assert validate_refs(None, repo_url=None) == []
    assert validate_refs([], repo_url=None) == []


def test_valid_ordered_refs_preserved():
    assert validate_refs([9, 2, 5], repo_url="https://github.com/o/r") == [9, 2, 5]


# ---- goal.yaml round trip ---------------------------------------------------


def test_goal_yaml_round_trips_issue_refs(tmp_path):
    store = GoalStore(tmp_path, now=Clock())
    store.create_goal(
        "g", objective="Fix the referenced issues.", workspace_dir="/repos/demo",
        repo_url="https://github.com/o/r", done_when="issues resolved",
        issue_refs=[7, 3],
    )
    g = store.load_goal("g")
    assert g.issue_refs == [7, 3]


def test_pre_019_goal_yaml_loads_as_the_issue_less_lane(tmp_path):
    """US5's byte-compat pin: a goal.yaml with no issue_refs key (every goal
    authored before spec 019) loads with an empty list — the issue-less lane,
    behavior unchanged."""
    seed_goal(tmp_path, "old")
    store = GoalStore(tmp_path, now=Clock())
    assert store.load_goal("old").issue_refs == []


# ---- service doorway --------------------------------------------------------


def _service(tmp_path):
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    return GoalService(TaskQueue(db), db, config=cfg), db


_SAGA = dict(out_of_scope=[], invariants=[], established=[])


def test_create_goal_threads_issues_to_the_record_and_display(tmp_path):
    svc, db = _service(tmp_path)
    try:
        svc.create_goal(
            "g", objective="Fix the referenced issues end to end.",
            workspace_dir=str(tmp_path / "ws"),
            repo_url="https://github.com/o/r",
            done_when="the referenced issues' behavior holds",
            backlog=[], mode="one_shot", issues=[11, 4], **_SAGA,
        )
        assert svc.get_goal("g")["issue_refs"] == [11, 4]
    finally:
        db.close()


def test_create_goal_refuses_bad_refs_and_persists_nothing(tmp_path):
    svc, db = _service(tmp_path)
    try:
        with pytest.raises(ValueError):
            svc.create_goal(
                "g", objective="Fix the referenced issues end to end.",
                workspace_dir=str(tmp_path / "ws"),
                repo_url="https://github.com/o/r",
                done_when="the referenced issues' behavior holds",
                backlog=[], mode="one_shot", issues=[4, 4], **_SAGA,
            )
        with pytest.raises(KeyError):
            svc.get_goal("g")   # nothing persisted on refusal
    finally:
        db.close()


# ---- the length budget (spec 019 US3) --------------------------------------


@pytest.mark.asyncio
async def test_over_budget_referenced_goal_refused_with_relocation_message(tmp_path):
    svc, db = _service(tmp_path)
    try:
        kw = dict(_SAGA, workspace_dir=str(tmp_path / "ws"),
                  repo_url="https://github.com/o/r",
                  done_when="GET /health returns 200 and is covered by a named test", backlog=[], mode="one_shot")
        with pytest.raises(ValueError) as exc:
            svc.create_goal("g", objective="x" * 1500, issues=[7], **kw)
        msg = str(exc.value)
        # the refusal names rule + input + destination + fixing verb (FR-010)
        assert "1500 chars" in msg and "1000-char budget" in msg
        assert "#7" in msg and "regrade_intake" in msg
        with pytest.raises(KeyError):
            svc.get_goal("g")
    finally:
        db.close()


def test_within_budget_referenced_goal_accepted(tmp_path):
    svc, db = _service(tmp_path)
    try:
        svc.create_goal(
            "g", objective="Fix #7 end to end.", issues=[7],
            workspace_dir=str(tmp_path / "ws"), repo_url="https://github.com/o/r",
            done_when="GET /health returns 200 and is covered by a named test", backlog=[], mode="one_shot", **_SAGA)
        assert svc.get_goal("g")["issue_refs"] == [7]
    finally:
        db.close()


def test_issue_less_goal_exempt_from_budget(tmp_path):
    """US5's protection: the essay stays legal WITHOUT refs (bench/greenfield
    scoping is real work) — the budget targets essay-plus-issue only."""
    svc, db = _service(tmp_path)
    try:
        svc.create_goal(
            "g", objective="long scoping essay " * 200,
            workspace_dir=str(tmp_path / "ws"), repo_url="https://github.com/o/r",
            done_when="GET /health returns 200 and is covered by a named test", backlog=[], mode="one_shot", **_SAGA)
        assert svc.get_goal("g")["issue_refs"] == []
    finally:
        db.close()


def test_budget_configurable_via_config_doorway(tmp_path, monkeypatch):
    svc, db = _service(tmp_path)
    try:
        monkeypatch.setenv("DEVCLAW_GOAL_TEXT_BUDGET", "40")
        with pytest.raises(ValueError):
            svc.create_goal(
                "g", objective="x" * 41, issues=[7],
                workspace_dir=str(tmp_path / "ws"), repo_url="https://github.com/o/r",
                done_when="GET /health returns 200 and is covered by a named test", backlog=[], mode="one_shot", **_SAGA)
    finally:
        db.close()


# ---- readiness gate + exclusivity (spec 019 US4) ---------------------------


def _ready_snap(n, *, body="ctx\n## Acceptance\n- holds\n", state="open"):
    from devclaw.goal.issue_ref import IssueSnapshot
    from devclaw.intake import READY_LABEL
    return IssueSnapshot(number=n, title="t", body=body, state=state,
                         labels=(READY_LABEL, "P1"))


def _unready_snap(n, **kw):
    from devclaw.goal.issue_ref import IssueSnapshot
    return IssueSnapshot(number=n, title="t", body="b", state="open",
                         labels=("needs-refinement",))


_KW4 = dict(
    objective="Fix the referenced issue.",
    backlog=[], mode="one_shot",
    done_when="GET /health returns 200 and is covered by a named test",
)


@pytest.mark.asyncio
async def test_unready_ref_refused_naming_grading_verb(tmp_path):
    from tests.goal_fakes import FakeIssueFetcher

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _unready_snap(7)})
        with pytest.raises(ValueError) as exc:
            await svc.create_goal_async(
                "g", issues=[7], workspace_dir=str(tmp_path / "ws"),
                repo_url="https://github.com/o/r", **_KW4, **_SAGA)
        msg = str(exc.value)
        assert "not graded ready" in msg
        assert "grade_backlog" in msg and "regrade_intake" in msg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ready_ref_accepted_after_grading(tmp_path):
    from tests.goal_fakes import FakeIssueFetcher

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _ready_snap(7)})
        await svc.create_goal_async(
            "g", issues=[7], workspace_dir=str(tmp_path / "ws"),
            repo_url="https://github.com/o/r", **_KW4, **_SAGA)
        assert svc.get_goal("g")["issue_refs"] == [7]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_second_live_goal_on_same_issue_refused_naming_holder(tmp_path):
    from tests.goal_fakes import FakeIssueFetcher

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _ready_snap(7), 8: _ready_snap(8)})
        kw = dict(workspace_dir=str(tmp_path / "ws"),
                  repo_url="https://github.com/o/r", **_KW4, **_SAGA)
        await svc.create_goal_async("holder", issues=[7], **kw)
        with pytest.raises(ValueError) as exc:
            await svc.create_goal_async("second", issues=[7, 8], **kw)
        msg = str(exc.value)
        assert "#7" in msg and "'holder'" in msg and "cancel_goal" in msg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_done_goal_releases_its_issue(tmp_path):
    from devclaw.goal.models import GoalStatus
    from tests.goal_fakes import FakeIssueFetcher

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _ready_snap(7)})
        kw = dict(workspace_dir=str(tmp_path / "ws"),
                  repo_url="https://github.com/o/r", **_KW4, **_SAGA)
        await svc.create_goal_async("holder", issues=[7], **kw)
        svc._goal_store.save_status("holder", GoalStatus(phase="done"))
        await svc.create_goal_async("successor", issues=[7], **kw)
        assert svc.get_goal("successor")["issue_refs"] == [7]
    finally:
        db.close()
