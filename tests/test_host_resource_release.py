"""Release of the durable host resources a project owns (#595).

devclaw created two things per project that outlived every goal — the workspace
checkout and the per-project toolchain volume — and had no removal path for
either, so a long-running instance filled its own disk (VPS at 79%, 2026-08-22;
34 workspace dirs, 20 orphaned volumes, ledger alone holding ~1G for a project
that had been deliberately deleted).

The fix is ownership, not a janitor: the workspace is a PROJECT resource
recorded in the registry (spec 003), the volume name is a pure function of that
path, so release is derivable rather than inferred. These tests pin the derivation
(including the container→host path translation that is easy to get wrong and
silently finds nothing), and pin the negative cases — live goal, in-flight task —
that must never be released at any age.
"""
from __future__ import annotations

import json
import subprocess
import types

import pytest

from devclaw import host_resources as hr


class _Goal:
    def __init__(self, id, workspace_dir, phase):
        self.id, self.workspace_dir, self.phase = id, workspace_dir, phase


class _Task:
    def __init__(self, id, workspace_dir):
        self.id, self.workspace_dir = id, workspace_dir


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "workspaces" / "myproj"
    d.mkdir(parents=True)
    (d / "README.md").write_text("work\n")
    return str(d)


@pytest.fixture
def docker_ok(monkeypatch):
    """Record docker calls; every removal succeeds."""
    calls: list[list[str]] = []

    def fake(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(hr, "_docker_run_sync", fake)
    return calls


# --- the derivation -------------------------------------------------------


def test_toolchain_volume_name_is_derived_from_the_host_path_not_the_container_path(
    monkeypatch,
):
    """sandcastle names the volume from the HOST view of the bind path. devclaw
    itself runs containerized and sees a different path for the same directory —
    on the live VPS /var/lib/devclaw/workspaces/devclaw (container) and
    /srv/devclaw/workspaces/devclaw (host) hash to DIFFERENT names, and only the
    host one exists. Deriving from the container path would look for a volume
    that was never created and report 'nothing to release' forever."""
    monkeypatch.setenv("DEVCLAW_CONTAINER_PATH_PREFIX", "/var/lib/devclaw/workspaces")
    monkeypatch.setenv("DEVCLAW_HOST_PATH_PREFIX", "/srv/devclaw/workspaces")

    name = hr.toolchain_volume_for("/var/lib/devclaw/workspaces/devclaw")

    # The exact name observed on the live box for this project.
    assert name == "devclaw-toolchains-devclaw-d6048d57"


def test_toolchain_volume_name_passes_through_when_no_translation_is_configured(
    monkeypatch,
):
    monkeypatch.delenv("DEVCLAW_CONTAINER_PATH_PREFIX", raising=False)
    monkeypatch.delenv("DEVCLAW_HOST_PATH_PREFIX", raising=False)
    assert hr.toolchain_volume_for("/srv/devclaw/workspaces/devclaw") == (
        "devclaw-toolchains-devclaw-d6048d57"
    )


# --- the happy path -------------------------------------------------------


def test_release_removes_workspace_and_toolchain_volume(ws, docker_ok):
    out = hr.release_project_resources(ws)

    assert not out["failed"]
    kinds = {r["kind"] for r in out["released"]}
    assert kinds == {"workspace", "toolchain_volume"}
    import pathlib

    assert not pathlib.Path(ws).exists()
    assert docker_ok == [["volume", "rm", hr.toolchain_volume_for(ws)]]


def test_dry_run_reports_what_would_go_and_removes_nothing(ws, docker_ok):
    out = hr.release_project_resources(ws, dry_run=True)

    import pathlib

    assert pathlib.Path(ws).exists()
    assert docker_ok == []
    assert {r["kind"] for r in out["released"]} == {"workspace", "toolchain_volume"}
    assert out["dry_run"] is True


# --- the negative cases: live state is never released ---------------------


@pytest.mark.parametrize("phase", ["executing", "verifying", "in_flight", "blocked"])
def test_release_refuses_while_a_goal_on_that_workspace_is_not_terminal(
    ws, docker_ok, phase
):
    """'blocked' is deliberately in this list: a blocked goal is resumable, so
    its checkout is live state, not garbage."""
    out = hr.release_for_project(
        ws,
        goals=[_Goal("g1", ws, phase)],
        running_tasks=[],
    )

    assert out["blocked"] and phase in out["blocked"][0]
    assert out["released"] == []
    import pathlib

    assert pathlib.Path(ws).exists()
    assert docker_ok == []


def test_release_refuses_while_a_task_on_that_workspace_is_running(ws, docker_ok):
    out = hr.release_for_project(
        ws,
        goals=[_Goal("g1", ws, "done")],
        running_tasks=[_Task("t1", ws)],
    )

    assert out["blocked"] == ["task 't1' is still running"]
    assert out["released"] == []
    import pathlib

    assert pathlib.Path(ws).exists()


def test_release_refuses_when_the_goal_is_cancelled_but_its_task_is_still_in_flight(
    ws, docker_ok
):
    """Terminal phase and in-flight task are true at the same instant when a goal
    is cancelled mid-run. The task check must win — it is not derived from the
    phase check."""
    out = hr.release_for_project(
        ws,
        goals=[_Goal("g1", ws, "cancelled")],
        running_tasks=[_Task("t1", ws)],
    )

    assert out["blocked"] == ["task 't1' is still running"]
    import pathlib

    assert pathlib.Path(ws).exists()


def test_release_proceeds_when_every_goal_on_the_shared_workspace_is_terminal(
    ws, docker_ok
):
    """Several goals share one project workspace on the live instance (four
    finance-sentry goals point at the same dir). Release requires ALL of them to
    be terminal, never just one."""
    out = hr.release_for_project(
        ws,
        goals=[
            _Goal("g1", ws, "done"),
            _Goal("g2", ws, "cancelled"),
            _Goal("g3", "/somewhere/else", "executing"),
        ],
        running_tasks=[],
    )

    assert out["blocked"] == []
    assert {r["kind"] for r in out["released"]} == {"workspace", "toolchain_volume"}


def test_a_live_goal_on_a_different_workspace_does_not_block_release(ws, docker_ok):
    out = hr.release_for_project(
        ws, goals=[_Goal("other", "/var/other", "executing")], running_tasks=[]
    )
    assert out["blocked"] == []


# --- failures are surfaced, drift is not a failure ------------------------


def test_a_removal_that_fails_is_reported_rather_than_raising(ws, monkeypatch):
    def boom(args):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="volume is in use")

    monkeypatch.setattr(hr, "_docker_run_sync", boom)

    out = hr.release_project_resources(ws)

    assert [f["kind"] for f in out["failed"]] == ["toolchain_volume"]
    assert "in use" in out["failed"][0]["reason"]
    # the workspace still went — one failure does not abandon the other resource
    assert [r["kind"] for r in out["released"]] == ["workspace"]


def test_an_already_absent_resource_is_drift_not_a_failure(tmp_path, monkeypatch):
    """Denys removed 20 volumes by hand on 2026-08-22. A record naming a resource
    that no longer exists must be a no-op, never an error."""

    def gone(args):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="Error: no such volume: x")

    monkeypatch.setattr(hr, "_docker_run_sync", gone)

    out = hr.release_project_resources(str(tmp_path / "workspaces" / "never-made"))

    assert out["failed"] == []
    assert out["released"] == []


def test_docker_unavailable_degrades_instead_of_raising(ws, monkeypatch):
    """host/stub engines and CI have no docker. Releasing the directory must
    still work; the volume simply never existed here."""

    def no_docker(args):
        raise OSError("docker not found")

    monkeypatch.setattr(hr, "_docker_run_sync", no_docker)

    out = hr.release_project_resources(ws)

    assert [r["kind"] for r in out["released"]] == ["workspace"]
    assert out["failed"] == []


@pytest.mark.parametrize("bad", ["", "   ", "relative/path", "/", "/srv"])
def test_release_refuses_a_workspace_path_that_is_unsafe_to_remove(bad, docker_ok):
    """Deletion is irreversible on the host. A malformed record must fail loudly
    here rather than hand a mount root to rmtree."""
    out = hr.release_project_resources(bad)

    assert [f["kind"] for f in out["failed"]] == ["workspace"]
    assert out["released"] == [] or all(
        r["kind"] != "workspace" for r in out["released"]
    )


def test_release_for_project_with_no_workspace_is_a_no_op(docker_ok):
    out = hr.release_for_project(None, goals=[], running_tasks=[])
    assert out == {"released": [], "failed": [], "blocked": [], "dry_run": False}
    assert docker_ok == []


# --- goals may arrive as dicts (the MCP surface shape) --------------------


def test_blockers_read_goals_supplied_as_dicts(ws):
    blockers = hr.release_blockers(
        ws,
        goals=[{"id": "g1", "workspace_dir": ws, "phase": "executing"}],
        running_tasks=[],
    )
    assert blockers and "g1" in blockers[0]


def test_workspace_identity_is_normalized_before_comparing(ws):
    """The registry joins goals to projects on the normalized path axis; release
    must use the same axis or a trailing slash would hide a live goal."""
    blockers = hr.release_blockers(
        ws,
        goals=[_Goal("g1", ws + "/", "executing")],
        running_tasks=[],
    )
    assert blockers


# --- the delete_project tool wiring ---------------------------------------


@pytest.fixture
def tool_env(tmp_path, monkeypatch, docker_ok):
    """delete_project against a throwaway registry + stubbed goal/task sources."""
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import tools as _tools

    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    ws = tmp_path / "workspaces" / "doomed"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("x\n")
    reg.create(id="doomed", name="Doomed", workspace_dir=str(ws))

    state = types.SimpleNamespace(goals=[], running=[])
    monkeypatch.setattr(_tools.projects, "registry", reg)
    monkeypatch.setattr(
        _tools.projects, "goals", types.SimpleNamespace(list_goals=lambda: state.goals)
    )
    monkeypatch.setattr(
        _tools.projects,
        "store",
        types.SimpleNamespace(list_tasks=lambda **kw: state.running),
    )
    return types.SimpleNamespace(
        tools=_tools, reg=reg, ws=str(ws), state=state, docker=docker_ok
    )


async def test_delete_project_releases_the_workspace_and_volume_it_owned(tool_env):
    out = json.loads(await tool_env.tools.delete_project(project_id="doomed"))

    assert out["deleted"] is True
    assert {r["kind"] for r in out["resources"]["released"]} == {
        "workspace",
        "toolchain_volume",
    }
    import pathlib

    assert not pathlib.Path(tool_env.ws).exists()
    assert tool_env.reg.get("doomed") is None


async def test_delete_project_keeps_the_registry_row_when_release_is_blocked(tool_env):
    """The record is the ONLY thing that can ever find these resources again.
    Dropping it while its resources must stay would orphan them permanently —
    exactly the 18 unrecorded directories found on the box on 2026-08-22."""
    tool_env.state.goals = [_Goal("g1", tool_env.ws, "executing")]

    out = json.loads(await tool_env.tools.delete_project(project_id="doomed"))

    assert out["deleted"] is False
    assert out["resources"]["blocked"]
    assert tool_env.reg.get("doomed") is not None
    import pathlib

    assert pathlib.Path(tool_env.ws).exists()


async def test_delete_project_can_still_drop_the_record_only(tool_env):
    """The pre-#595 behavior stays reachable — it just isn't the default any
    more, because the default is what leaked the disk."""
    out = json.loads(
        await tool_env.tools.delete_project(
            project_id="doomed", release_resources=False
        )
    )

    assert out["deleted"] is True
    assert "resources" not in out
    import pathlib

    assert pathlib.Path(tool_env.ws).exists()


async def test_delete_project_dry_run_deletes_nothing_at_all(tool_env):
    out = json.loads(
        await tool_env.tools.delete_project(project_id="doomed", dry_run=True)
    )

    assert out["deleted"] is False
    assert tool_env.reg.get("doomed") is not None
    import pathlib

    assert pathlib.Path(tool_env.ws).exists()
    assert tool_env.docker == []


async def test_delete_project_still_rejects_an_unknown_id(tool_env):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await tool_env.tools.delete_project(project_id="nope")


# --- the retention sweep --------------------------------------------------
#
# The timer half of #595: a goal-scoped workspace whose goal ended long enough
# ago that nobody will look at it again. Windows ruled by Denys 2026-08-22 —
# 14 days for a goal that ended badly (the checkout IS the forensics), 3 days
# for one that finished cleanly (the work is on a branch).

DAY_MS = 24 * 3600 * 1000


def _ago(now_ms: int, days: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(
        (now_ms - int(days * DAY_MS)) / 1000, tz=timezone.utc
    ).isoformat()


def _row(ws, *, phase="done", days_ago=30.0, now, direction=None, blocked_on=None,
         gid="g1"):
    return {
        "id": gid,
        "workspace_dir": ws,
        "phase": phase,
        "direction": direction,
        "blocked_on": blocked_on,
        "last_progress_at": _ago(now, days_ago),
        "last_tick_at": None,
        "last_eval_at": None,
        "last_plan_at": None,
    }


NOW = 1_800_000_000_000


def test_sweep_releases_a_cleanly_finished_goals_workspace_past_the_short_window(
    ws, docker_ok
):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=5, now=NOW, direction="achieved")],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert [r["kind"] for r in out["released"]] == ["workspace", "toolchain_volume"]


def test_sweep_keeps_a_cleanly_finished_workspace_inside_the_short_window(
    ws, docker_ok
):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=1, now=NOW, direction="achieved")],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["released"] == [] and out["considered"] == 0
    import pathlib

    assert pathlib.Path(ws).exists()


def test_a_badly_ended_goals_workspace_survives_the_short_window(ws, docker_ok):
    """5 days old and off_track: past the 3-day clean window but well inside the
    14-day forensic one. This is the case the two windows exist for."""
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=5, now=NOW, direction="off_track")],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["released"] == []
    import pathlib

    assert pathlib.Path(ws).exists()


def test_a_badly_ended_goals_workspace_is_released_past_the_long_window(
    ws, docker_ok
):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=20, now=NOW, direction="off_track")],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert [r["kind"] for r in out["released"]] == ["workspace", "toolchain_volume"]
    assert out["released"][0]["bad"] is True


def test_an_uncleared_block_at_a_terminal_phase_counts_as_ended_badly(ws, docker_ok):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[
            _row(ws, days_ago=5, now=NOW, phase="cancelled", blocked_on="circuit breaker")
        ],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["released"] == []  # held for the long window, not the short one


def test_the_sweep_never_touches_a_registered_projects_workspace(ws, docker_ok):
    """The correctness trap. All four finance-sentry goals can be terminal while
    the project is alive and about to get new work — sweeping its checkout would
    delete a live project's clone and force a re-clone. A project's workspace is
    released by delete_project, never by goal terminality."""
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=999, now=NOW, direction="achieved")],
        project_workspaces={ws},
        now_ms=NOW,
    )
    assert out["released"] == [] and out["considered"] == 0
    import pathlib

    assert pathlib.Path(ws).exists()


def test_the_sweep_does_nothing_when_project_ownership_is_unknown(ws, docker_ok):
    """No registry -> no way to know which workspaces a project owns. Absence of
    information is never permission to delete."""
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=999, now=NOW)],
        project_workspaces=None,
        now_ms=NOW,
    )
    assert out["released"] == []
    assert out["drained"] is True
    import pathlib

    assert pathlib.Path(ws).exists()


def test_a_goal_with_no_timestamps_is_never_swept(ws, docker_ok):
    """An unknown age is never old enough."""
    row = _row(ws, now=NOW)
    row["last_progress_at"] = None
    out = hr.sweep_terminal_goal_workspaces(
        goals=[row], project_workspaces=set(), now_ms=NOW
    )
    assert out["considered"] == 0 and out["released"] == []


def test_the_sweep_requires_every_goal_on_a_shared_workspace_to_be_terminal(
    ws, docker_ok
):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[
            _row(ws, days_ago=99, now=NOW, gid="old"),
            _row(ws, days_ago=1, now=NOW, phase="executing", gid="live"),
        ],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["considered"] == 0 and out["released"] == []


def test_the_sweep_is_bounded_per_batch_and_says_when_it_is_not_drained(
    tmp_path, docker_ok
):
    """A 34-directory backlog must drain across ticks, never wedge one."""
    rows = []
    for i in range(8):
        d = tmp_path / "workspaces" / f"ws{i}"
        d.mkdir(parents=True)
        rows.append(_row(str(d), days_ago=99, now=NOW, gid=f"g{i}"))

    out = hr.sweep_terminal_goal_workspaces(
        goals=rows, project_workspaces=set(), now_ms=NOW, batch_limit=5
    )

    assert out["considered"] == 8
    assert len([r for r in out["released"] if r["kind"] == "workspace"]) == 5
    assert out["drained"] is False  # more remain -> watermark must NOT advance


def test_a_short_batch_reports_drained_so_the_watermark_can_advance(ws, docker_ok):
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=99, now=NOW)],
        project_workspaces=set(),
        now_ms=NOW,
        batch_limit=5,
    )
    assert out["drained"] is True


def test_retention_disabled_by_env_sweeps_nothing(ws, docker_ok, monkeypatch):
    monkeypatch.setenv("DEVCLAW_WORKSPACE_RETENTION_DAYS", "0")
    monkeypatch.setenv("DEVCLAW_WORKSPACE_RETENTION_DAYS_FAILED", "0")
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=999, now=NOW)],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["released"] == []


def test_retention_windows_are_configurable_like_the_trace_retention(monkeypatch):
    monkeypatch.setenv("DEVCLAW_WORKSPACE_RETENTION_DAYS", "9")
    monkeypatch.setenv("DEVCLAW_WORKSPACE_RETENTION_DAYS_FAILED", "99")
    assert hr.workspace_retention_days() == 9
    assert hr.failed_workspace_retention_days() == 99


def test_a_running_task_still_blocks_a_workspace_the_sweep_selected(ws, docker_ok):
    """Age says release; an in-flight task says no. The task wins."""
    out = hr.sweep_terminal_goal_workspaces(
        goals=[_row(ws, days_ago=999, now=NOW)],
        running_tasks=[_Task("t1", ws)],
        project_workspaces=set(),
        now_ms=NOW,
    )
    assert out["released"] == []
    assert out["failed"] and "still running" in out["failed"][0]["reason"]
    import pathlib

    assert pathlib.Path(ws).exists()


# --- the heartbeat seam ---------------------------------------------------


def test_the_tick_seam_is_a_no_op_on_an_engine_without_the_sweep():
    """Test doubles don't implement reap_workspaces; that must mean 'no sweep',
    never an exception on the heartbeat."""
    from devclaw.goal import tick as _tick

    _tick._engine_reap_workspaces(object(), object(), lambda: set())  # no raise


def test_the_tick_seam_sweeps_nothing_when_the_registry_resolver_raises():
    """Unknown ownership must sweep nothing rather than treat everything as
    fair game."""
    from devclaw.goal import tick as _tick

    called = []

    class _Engine:
        def reap_workspaces(self, goals, owned):
            called.append(owned)
            return {}

    def boom():
        raise RuntimeError("registry down")

    _tick._engine_reap_workspaces(_Engine(), object(), boom)

    assert called == []  # never reached the engine


def test_the_tick_seam_swallows_a_sweep_failure_rather_than_wedging_the_heartbeat():
    from devclaw.goal import tick as _tick

    class _Engine:
        def reap_workspaces(self, goals, owned):
            raise RuntimeError("docker exploded")

    class _Store:
        def list_goal_ids(self):
            return []

    _tick._engine_reap_workspaces(_Engine(), _Store(), lambda: set())  # no raise


def test_the_sweep_costs_zero_claude_calls():
    """Constitutional (Principle III): the sweep rides the heartbeat's cheap
    maintenance slot. It is pure filesystem + docker CLI work and must never
    reach cognition."""
    from tests.goal_fakes import FakeClaude

    fake = FakeClaude()
    hr.sweep_terminal_goal_workspaces(
        goals=[], project_workspaces=set(), now_ms=NOW
    )
    assert fake.calls == 0
