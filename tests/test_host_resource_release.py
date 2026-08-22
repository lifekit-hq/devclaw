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
    monkeypatch.setattr(_tools, "registry", reg)
    monkeypatch.setattr(
        _tools, "goals", types.SimpleNamespace(list_goals=lambda: state.goals)
    )
    monkeypatch.setattr(
        _tools,
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
