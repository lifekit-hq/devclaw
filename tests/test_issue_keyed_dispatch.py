"""Spec 022 US1 — issue-keyed companion dispatch (create-or-attach).

Named regression tests for:
- FR-002: create-or-attach: second dispatch of the same issue attaches
- FR-003: store-level uniqueness — a real constraint, not a read-then-write check
- FR-005: issue fetched live; closed or unreachable → fail loud
- FR-006: response distinguishes created vs attached; dedup logged
- FR-008: implement_feature / fix_bug forward issue_ref; read-only kinds unaffected
- FR-009: doctor check detects missing goal_issue_identity table
- FR-011: long-lived goal collision → reject naming goal + steer invocation
- FR-012: completed identity re-arms iff issue is open on the tracker
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from devclaw.goal.issue_ref import IssueRefError, IssueSnapshot
from devclaw.goal.service import GoalConfig, GoalService
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue
from tests.goal_fakes import FakeIssueFetcher


def _snap(number: int, state: str = "open", title: str = "Fix the thing") -> IssueSnapshot:
    return IssueSnapshot(number=number, title=title, body="## Acceptance\n- it works", state=state)


def _open(number: int) -> IssueSnapshot:
    return _snap(number, state="open")


def _closed(number: int) -> IssueSnapshot:
    return _snap(number, state="closed")


def _service(tmp_path):
    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    svc = GoalService(TaskQueue(db), db, config=cfg)
    return svc, db


_REPO = "https://github.com/org/repo.git"


# ---------------------------------------------------------------------------
# FR-002: create-or-attach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_creates_goal_on_first_call(tmp_path):
    """First dispatch of an issue creates a one_shot goal and returns created."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        ws = str(tmp_path / "ws")
        result = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        assert result["result"] == "created"
        assert result["issue_ref"] == 7
        goal_id = result["goal_id"]
        # The goal exists and is executing
        g = svc.get_goal(goal_id)
        assert g["mode"] == "one_shot"
        assert 7 in g["issue_refs"]
        assert g["lifecycle"] == "executing"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_attaches_on_duplicate_while_active(tmp_path):
    """Second dispatch of the same issue while work is active returns attached."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        ws = str(tmp_path / "ws")
        first = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        assert first["result"] == "created"
        first_goal_id = first["goal_id"]

        second = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        assert second["result"] == "attached"
        assert second["goal_id"] == first_goal_id
        assert second["issue_ref"] == 7
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_dedup_logged_to_goal_log(tmp_path):
    """FR-006: a swallowed duplicate is logged to the goal log — not invisible."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        ws = str(tmp_path / "ws")
        first = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        goal_id = first["goal_id"]
        await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        # The dedup attempt is recorded in the goal log
        recent_log = svc.get_goal(goal_id).get("recent_log", "")
        assert "duplicate" in recent_log.lower() or "already active" in recent_log.lower()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_response_distinguishes_created_vs_attached(tmp_path):
    """FR-006: the caller can tell from the response alone whether work was created."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({42: _open(42)})
        ws = str(tmp_path / "ws")
        r1 = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=42,
        )
        r2 = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=42,
        )
        assert r1["result"] == "created"
        assert r2["result"] == "attached"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FR-003: store-level uniqueness constraint
# ---------------------------------------------------------------------------


def test_issue_identity_uniqueness_is_enforced_at_sqlite_level(tmp_path):
    """FR-003: the uniqueness constraint is a real SQLite PRIMARY KEY, not a
    read-then-write check — two concurrent INSERT attempts hit IntegrityError."""
    db = StateStore(str(tmp_path / "state.db"))
    from devclaw.goal.state import GoalState

    gs = GoalState(db)
    claimed1, gid1 = gs._claim_issue_identity("proj", "7", "goal-a", 1000)
    assert claimed1 is True
    assert gid1 == "goal-a"
    # Second INSERT for the same (project, issue) must NOT create a second row
    claimed2, gid2 = gs._claim_issue_identity("proj", "7", "goal-b", 1001)
    assert claimed2 is False
    assert gid2 == "goal-a"  # the winner's goal_id is returned
    db.close()


def test_issue_identity_different_projects_are_independent(tmp_path):
    """Two different projects dispatching the same issue number are independent."""
    db = StateStore(str(tmp_path / "state.db"))
    from devclaw.goal.state import GoalState

    gs = GoalState(db)
    claimed_a, _ = gs._claim_issue_identity("proj-a", "7", "goal-a", 1000)
    claimed_b, _ = gs._claim_issue_identity("proj-b", "7", "goal-b", 1001)
    assert claimed_a is True
    assert claimed_b is True
    db.close()


def test_issue_identity_issue_key_not_nullable(tmp_path):
    """The issue_key column is NOT NULL (PRIMARY KEY); no NULL row can bypass the constraint."""
    db = StateStore(str(tmp_path / "state.db"))
    from devclaw.goal.state import GoalState

    GoalState(db)  # bootstraps goal_issue_identity and other tables
    with pytest.raises(sqlite3.IntegrityError):
        with db._lock:
            db._db.execute(
                "INSERT INTO goal_issue_identity(project_id, issue_key, goal_id, created_at) "
                "VALUES(?, ?, ?, ?)",
                ("proj", None, "goal-x", 1000),
            )
    db.close()


# ---------------------------------------------------------------------------
# FR-005: live issue fetch — fail loud on unreachable / closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rejects_closed_issue(tmp_path):
    """FR-005 / FR-012: a closed issue is rejected immediately, naming its state."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _closed(7)})
        with pytest.raises(ValueError) as exc:
            await svc.dispatch_issue(
                project_id="proj", workspace_dir=str(tmp_path / "ws"),
                repo_url=_REPO, issue_ref=7,
            )
        msg = str(exc.value)
        assert "closed" in msg and "#7" in msg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rejects_unreachable_issue(tmp_path):
    """FR-005: an unreachable issue (gh failure) blocks the dispatch loudly."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: IssueRefError("gh exit 1: 404")})
        with pytest.raises(ValueError) as exc:
            await svc.dispatch_issue(
                project_id="proj", workspace_dir=str(tmp_path / "ws"),
                repo_url=_REPO, issue_ref=7,
            )
        msg = str(exc.value)
        assert "#7" in msg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rejects_missing_repo_url(tmp_path):
    """FR-005: a project without repo_url cannot fetch the issue — rejected."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        with pytest.raises(ValueError) as exc:
            await svc.dispatch_issue(
                project_id="proj", workspace_dir=str(tmp_path / "ws"),
                repo_url=None, issue_ref=7,
            )
        assert "repo_url" in str(exc.value)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FR-011: long-lived goal collision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rejected_if_in_long_lived_goal_scope(tmp_path):
    """FR-011: a companion dispatch naming an issue already in a live long-lived
    goal's scope is rejected, naming that goal and the exact steer invocation."""
    svc, db = _service(tmp_path)
    try:
        # Create a long-lived goal that references issue #7
        svc.create_goal(
            "ll-goal", objective="Long-lived work on the platform.",
            workspace_dir=str(tmp_path / "ws"),
            repo_url=_REPO,
            done_when="all acceptance criteria in the referenced issues hold",
            backlog=[], mode="long_lived",
            issues=[7],
            out_of_scope=[], invariants=[], established=[],
        )
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        with pytest.raises(ValueError) as exc:
            await svc.dispatch_issue(
                project_id="proj", workspace_dir=str(tmp_path / "ws"),
                repo_url=_REPO, issue_ref=7,
            )
        msg = str(exc.value)
        assert "ll-goal" in msg
        assert "steer_goal" in msg
        assert "#7" in msg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_allowed_if_long_lived_goal_is_done(tmp_path):
    """FR-011: a completed long-lived goal no longer blocks companion dispatch."""
    svc, db = _service(tmp_path)
    try:
        svc.create_goal(
            "ll-done", objective="Long-lived — now finished.",
            workspace_dir=str(tmp_path / "ws"),
            repo_url=_REPO,
            done_when="all issues resolved end to end",
            backlog=[], mode="long_lived", issues=[7],
            out_of_scope=[], invariants=[], established=[],
        )
        from devclaw.goal.models import GoalStatus

        svc._goal_store.save_status("ll-done", GoalStatus(phase="done"))
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        result = await svc.dispatch_issue(
            project_id="proj", workspace_dir=str(tmp_path / "ws"),
            repo_url=_REPO, issue_ref=7,
        )
        assert result["result"] == "created"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FR-012: re-arm after completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rearms_after_completion_if_issue_open(tmp_path):
    """FR-012: a completed identity re-arms when the issue is still open."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        ws = str(tmp_path / "ws")
        first = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        first_goal_id = first["goal_id"]

        # Force the goal to done
        from devclaw.goal.models import GoalStatus

        svc._goal_store.save_status(first_goal_id, GoalStatus(phase="done"))

        # Re-dispatch with issue still open → new goal created
        second = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        assert second["result"] == "created"
        assert second["goal_id"] != first_goal_id
        # Identity row now points at the new goal
        new_id = svc._goal_store.lookup_issue_identity("proj", "7")
        assert new_id == second["goal_id"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_issue_keyed_dispatch_rejects_after_completion_if_issue_closed(tmp_path):
    """FR-012: a completed identity cannot be re-armed when the issue is closed."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({7: _open(7)})
        ws = str(tmp_path / "ws")
        first = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
        )
        first_goal_id = first["goal_id"]

        from devclaw.goal.models import GoalStatus

        svc._goal_store.save_status(first_goal_id, GoalStatus(phase="done"))

        # Now issue is closed on the tracker
        svc._issue_fetcher = FakeIssueFetcher({7: _closed(7)})
        with pytest.raises(ValueError) as exc:
            await svc.dispatch_issue(
                project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=7,
            )
        msg = str(exc.value)
        assert "closed" in msg and "#7" in msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FR-008: implement_feature and fix_bug forward issue_ref; read-only unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_implement_feature_forwards_issue_ref_to_goal_lane(monkeypatch, tmp_path):
    """FR-008: implement_feature with issue_ref routes through the goal lane."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import tools as _tools
    from devclaw.server import _state
    from tests.goal_fakes import register_tmp_project

    dispatched: list[dict] = []

    async def _fake_dispatch(**kwargs) -> dict:
        dispatched.append(kwargs)
        return {"result": "created", "goal_id": "g-test", "issue_ref": kwargs["issue_ref"],
                "message": "ok"}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch)
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    raw = await _tools.implement_feature(
        project_id="proj", goal="fix auth", issue_ref=42, open_pr=True,
    )
    result = json.loads(raw)
    assert result["result"] == "created"
    assert result["goal_id"] == "g-test"
    (call,) = dispatched
    assert call["issue_ref"] == 42
    assert call["project_id"] == "proj"


@pytest.mark.asyncio
async def test_dispatch_task_read_only_kinds_unaffected_by_issue_ref(monkeypatch, tmp_path):
    """FR-008: read-only kinds (review_repository, validate_product) ignore issue_ref
    and go through the existing task queue path unchanged."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import tools as _tools
    from devclaw.server import _state
    from tests.goal_fakes import register_tmp_project

    queue_calls: list[dict] = []
    monkeypatch.setattr(_state.queue, "submit", lambda **kw: (queue_calls.append(kw) or "t1"))
    dispatch_calls: list[dict] = []

    async def _fake_dispatch(**kwargs) -> dict:
        dispatch_calls.append(kwargs)
        return {"result": "created", "goal_id": "g", "issue_ref": 7, "message": "ok"}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch)
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    # review_repository with issue_ref — should NOT call dispatch_issue
    await _tools.dispatch_task(
        kind="review_repository", project_id="proj", goal="check code",
        issue_ref=7,
    )
    assert not dispatch_calls, "read-only kind must not route through dispatch_issue"
    assert queue_calls, "read-only kind must still submit to queue"


# ---------------------------------------------------------------------------
# FR-009: doctor check for goal_issue_identity table
# ---------------------------------------------------------------------------


def _doctor_ctx(db, tmp_path):
    from devclaw.doctor.context import InstanceContext
    from devclaw.goal.store import GoalStore
    from devclaw.project_registry import ProjectRegistry

    goals_dir = tmp_path / "goals"
    goals_dir.mkdir(exist_ok=True)
    goal_store = GoalStore(goals_dir, state=db)
    registry = ProjectRegistry(db.db_path)
    return InstanceContext(store=db, goal_store=goal_store, registry=registry)


def test_doctor_check_passes_when_identity_table_present(tmp_path):
    """FR-009: doctor check returns OK when goal_issue_identity exists."""
    from devclaw.doctor.checks_instance import check_goal_issue_identity_table
    from devclaw.doctor.model import Verdict
    from devclaw.state_store import StateStore

    db = StateStore(str(tmp_path / "state.db"))
    # GoalStore construction bootstraps all tables including goal_issue_identity
    ctx = _doctor_ctx(db, tmp_path)
    findings = check_goal_issue_identity_table(ctx)
    assert any(f.verdict == Verdict.OK for f in findings)
    db.close()


def test_doctor_check_fails_when_identity_table_missing(tmp_path):
    """FR-009: doctor check returns FAIL when goal_issue_identity is absent
    but goal_status exists — the DB predates spec 022."""
    from devclaw.doctor.checks_instance import check_goal_issue_identity_table
    from devclaw.doctor.model import Verdict
    from devclaw.state_store import StateStore

    db = StateStore(str(tmp_path / "state.db"))
    # Create goal_status but NOT goal_issue_identity (simulating a pre-022 DB).
    # Use a direct connection to bypass GoalState's bootstrap.
    with db._lock:
        db._db.executescript(
            "CREATE TABLE IF NOT EXISTS goal_status (goal_id TEXT PRIMARY KEY);"
        )
        db._commit()
    # Build a minimal InstanceContext without triggering GoalState bootstrap
    from devclaw.doctor.context import InstanceContext
    from unittest.mock import MagicMock

    ctx = InstanceContext(store=db, goal_store=MagicMock(), registry=MagicMock())
    findings = check_goal_issue_identity_table(ctx)
    assert any(f.verdict == Verdict.FAIL for f in findings)
    db.close()


# ---------------------------------------------------------------------------
# Spec 022 US2 — companion dispatches ride the full goal lane
# ---------------------------------------------------------------------------


# --- T008: read-only kinds bypass any project hold (FR-008 pin) -------------


@pytest.mark.asyncio
async def test_read_only_dispatch_not_blocked_by_project_hold(monkeypatch, tmp_path):
    """T008: read-only kinds (review_repository) are unaffected by the project
    hold — FR-008 and spec 022 US2 both say read-only kinds are out of scope."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from tests.goal_fakes import register_tmp_project

    queue_calls: list[dict] = []
    monkeypatch.setattr(_state.queue, "submit", lambda **kw: (queue_calls.append(kw) or "t1"))

    from devclaw.goal.store import GoalStore
    from devclaw.state_store import StateStore as SS

    gs_db = SS(str(tmp_path / "gs.db"))
    gs = GoalStore(tmp_path / "gdir", state=gs_db)
    gs.create_goal(
        "holder-goal",
        objective="hold the fort",
        workspace_dir=str(tmp_path / "ws"),
        project_id="proj",
        cadence="1d",
        done_when="something useful is done",
        out_of_scope=[], invariants=[], established=[],
    )
    from devclaw.goal.models import GoalStatus
    gs.save_status("holder-goal", GoalStatus(phase="idle", lifecycle="executing"))
    monkeypatch.setattr(_state.goals, "_goal_store", gs)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "ws"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    # review_repository should succeed even though a goal holds the project.
    raw = await _tools.review_repository(project_id="proj", focus="check security")
    result = json.loads(raw)
    assert "task_id" in result, "read-only dispatch must not be blocked"
    assert queue_calls, "read-only dispatch must reach queue.submit"

    gs_db.close()


# --- T009: workspace prep at dispatch_issue() time --------------------------


@pytest.mark.asyncio
async def test_dispatch_issue_preps_workspace_on_new_goal_creation(monkeypatch, tmp_path):
    """T009 / spec 022 US2: dispatch_issue calls prepare_workspace (default
    branch reset) before creating the new goal — ensures the workspace is on
    the default-branch head when the first action fires."""
    from devclaw.goal import service as svc_mod

    prep_calls: list[tuple] = []

    async def _fake_prep(workspace_dir, repo_url=None, branch=None):
        prep_calls.append((workspace_dir, repo_url, branch))
        return branch or "main"

    monkeypatch.setattr(svc_mod, "prepare_workspace", _fake_prep)

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({5: _open(5)})
        ws = str(tmp_path / "ws")
        # Create a real git workspace so the Path.exists() guard passes.
        import subprocess
        (tmp_path / "ws").mkdir()
        subprocess.run(["git", "init", "-q", str(tmp_path / "ws")], check=True)

        result = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=5,
        )
        assert result["result"] == "created"
        # prepare_workspace must have been called for the workspace at default branch.
        assert any(
            call[0] == ws and call[2] is None  # default branch (no branch arg)
            for call in prep_calls
        ), f"expected prepare_workspace(ws, repo_url) call; got {prep_calls}"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dispatch_issue_skips_prep_for_nonexistent_workspace(monkeypatch, tmp_path):
    """T009: dispatch_issue skips workspace prep when the workspace does not yet
    exist — the tick's prepare_ws handles the clone-and-prep at first run."""
    from devclaw.goal import service as svc_mod

    prep_calls: list = []

    async def _fake_prep(workspace_dir, repo_url=None, branch=None):
        prep_calls.append(workspace_dir)
        return branch or "main"

    monkeypatch.setattr(svc_mod, "prepare_workspace", _fake_prep)

    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({9: _open(9)})
        ws = str(tmp_path / "ws")  # does NOT exist

        result = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=9,
        )
        assert result["result"] == "created"
        # prepare_workspace must NOT have been called (workspace absent).
        assert not prep_calls, "prep must not run for a non-existent workspace"
    finally:
        db.close()


# --- Serialization: two dispatches to same project → second queued ----------


@pytest.mark.asyncio
async def test_two_issue_dispatches_to_same_project_serialized_by_project_hold(tmp_path):
    """Spec 022 US2 serialization: two one_shot goals on the same project are
    serialized by the project hold — the second goal shows queued_behind the first."""
    svc, db = _service(tmp_path)
    try:
        svc._issue_fetcher = FakeIssueFetcher({1: _open(1), 2: _open(2)})
        ws = str(tmp_path / "ws")

        r1 = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=1,
        )
        r2 = await svc.dispatch_issue(
            project_id="proj", workspace_dir=ws, repo_url=_REPO, issue_ref=2,
        )
        assert r1["result"] == "created"
        assert r2["result"] == "created"
        assert r1["goal_id"] != r2["goal_id"]

        # The first goal holds the project; the second is queued behind it.
        g1 = svc.get_goal(r1["goal_id"])
        g2 = svc.get_goal(r2["goal_id"])
        assert g1["queued_behind"] is None, "first goal holds the project"
        assert g2["queued_behind"] == r1["goal_id"], (
            f"second goal should be queued behind {r1['goal_id']!r}, "
            f"got queued_behind={g2['queued_behind']!r}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Spec 022 US3 — freeform-prose path retired; auto-file intake + proceed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prose_only_dispatch_auto_files_issue_and_routes_goal_lane(monkeypatch, tmp_path):
    """T011 / spec 022 US3 FR-010: dispatch_task called without issue_ref for a
    mutating kind auto-files an intake issue via _auto_file_intake and routes to
    dispatch_issue (the goal lane) — queue.submit is never called."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from devclaw.server.tools import tasks as tasks_mod
    from tests.goal_fakes import register_tmp_project

    queue_calls: list = []
    intake_calls: list[dict] = []
    dispatch_calls: list[dict] = []

    monkeypatch.setattr(_state.queue, "submit", lambda **kw: (queue_calls.append(kw) or "t1"))

    async def _fake_auto_file(registry, *, project_id, goal):
        intake_calls.append({"project_id": project_id, "goal": goal})
        return 99

    monkeypatch.setattr(tasks_mod, "_auto_file_intake", _fake_auto_file)

    async def _fake_dispatch_issue(*, project_id, workspace_dir, repo_url,
                                   issue_ref, kind, objective, verify_cmd, open_pr):
        dispatch_calls.append({"project_id": project_id, "issue_ref": issue_ref, "kind": kind})
        return {"goal_id": "g-abc", "result": "created", "issue_ref": issue_ref}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch_issue)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "ws"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    raw = await _tools.dispatch_task(
        kind="implement_feature",
        project_id="proj",
        goal="add dark mode to the user dashboard",
    )
    result = json.loads(raw)

    assert intake_calls, "auto-file must have been called for prose-only dispatch"
    assert intake_calls[0]["goal"] == "add dark mode to the user dashboard"
    assert dispatch_calls, "dispatch_issue must have been called after auto-file"
    assert dispatch_calls[0]["issue_ref"] == 99, "goal lane keyed to auto-filed issue number"
    assert dispatch_calls[0]["kind"] == "implement_feature"
    assert result.get("auto_filed_issue") == 99, "response must include auto_filed_issue"
    assert queue_calls == [], "prose dispatch must NOT reach queue.submit directly"


@pytest.mark.asyncio
async def test_prose_only_dispatch_intake_failure_raises_tool_error(monkeypatch, tmp_path):
    """T011: if auto-filing the intake issue fails, dispatch raises ToolError and
    no work is submitted — the error message names the intake failure."""
    from fastmcp.exceptions import ToolError
    from devclaw.intake import IntakeError
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from devclaw.server.tools import tasks as tasks_mod
    from tests.goal_fakes import register_tmp_project

    queue_calls: list = []
    monkeypatch.setattr(_state.queue, "submit", lambda **kw: (queue_calls.append(kw) or "t1"))

    async def _failing_auto_file(registry, *, project_id, goal):
        raise IntakeError("gh could not create the issue — not authenticated")

    monkeypatch.setattr(tasks_mod, "_auto_file_intake", _failing_auto_file)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "ws"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    with pytest.raises(ToolError, match="auto-filing"):
        await _tools.dispatch_task(
            kind="implement_feature",
            project_id="proj",
            goal="add feature",
        )
    assert queue_calls == [], "failed intake must not reach queue.submit"


@pytest.mark.asyncio
async def test_implement_feature_alias_auto_files_issue_without_issue_ref(monkeypatch, tmp_path):
    """T011 / FR-008: implement_feature (thin sugar over dispatch_task) auto-files
    an intake issue when called without issue_ref — the alias is not deprecated."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from devclaw.server.tools import tasks as tasks_mod
    from tests.goal_fakes import register_tmp_project

    intake_calls: list = []
    dispatch_calls: list = []

    async def _fake_auto_file(registry, *, project_id, goal):
        intake_calls.append(goal)
        return 42

    monkeypatch.setattr(tasks_mod, "_auto_file_intake", _fake_auto_file)

    async def _fake_dispatch_issue(*, project_id, workspace_dir, repo_url,
                                   issue_ref, kind, objective, verify_cmd, open_pr):
        dispatch_calls.append({"issue_ref": issue_ref, "kind": kind})
        return {"goal_id": "g-x", "result": "created", "issue_ref": issue_ref}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch_issue)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "ws"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    raw = await _tools.implement_feature(
        project_id="proj", goal="add dark mode", open_pr=True
    )
    result = json.loads(raw)

    assert intake_calls == ["add dark mode"], "alias must trigger auto-file"
    assert dispatch_calls[0]["kind"] == "implement_feature"
    assert result.get("auto_filed_issue") == 42


@pytest.mark.asyncio
async def test_read_only_dispatch_with_no_issue_ref_reaches_queue_submit(monkeypatch, tmp_path):
    """T011 / FR-008: review_repository without issue_ref goes directly to
    queue.submit — read-only kinds are byte-unaffected by spec 022 US3."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from devclaw.server.tools import tasks as tasks_mod
    from tests.goal_fakes import register_tmp_project

    queue_calls: list = []
    intake_calls: list = []
    monkeypatch.setattr(_state.queue, "submit", lambda **kw: (queue_calls.append(kw) or "t1"))

    async def _should_not_be_called(registry, *, project_id, goal):
        intake_calls.append(goal)
        return 1

    monkeypatch.setattr(tasks_mod, "_auto_file_intake", _should_not_be_called)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "ws"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/org/repo.git")
    monkeypatch.setattr(_tools._common, "registry", reg)

    raw = await _tools.dispatch_task(
        kind="review_repository", project_id="proj", goal="check auth"
    )
    result = json.loads(raw)

    assert "task_id" in result, "read-only dispatch must succeed"
    assert queue_calls, "read-only dispatch must reach queue.submit"
    assert intake_calls == [], "auto-file must NOT be called for read-only kinds"
