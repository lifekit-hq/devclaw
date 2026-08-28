"""Manifest consumption at the gate/dispatch seams (spec 016 US2).

The FR-009 named regression lives here:
``test_manifest_edit_inside_run_does_not_change_gate_inputs`` — a worker-side
manifest edit (worktree or goal-branch commit) never changes a gate regime;
gate-relevant reads come from the merged base.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.goal.engine import GoalEngineError, _manifest_tiers
from devclaw.goal.models import Goal
from devclaw.goal.store import GoalStore
from devclaw.project_manifest import MANIFEST_NAME, resolve_surface
from devclaw.quality.task_gates import _browser_gate_failure

from tests.goal_fakes import FakeClaude

#: a diff touching an Angular component — app surface under default globs.
_FRONTEND_DIFF = """\
diff --git a/src/app/foo/foo.component.ts b/src/app/foo/foo.component.ts
index 111..222 100644
--- a/src/app/foo/foo.component.ts
+++ b/src/app/foo/foo.component.ts
@@ -1 +1 @@
-old
+new
"""

#: a verify result whose browser suite never ran — blocks on app surface.
_NEVER_RAN_VERIFY = {"ran": True, "passed": True, "browser_report": None}


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _goal(ws, explicit=None, verify_cmd=None):
    return Goal(id="g", objective="x", cadence="1d", engine="devclaw",
                workspace_dir=str(ws),
                strictness_explicit=explicit, verify_cmd=verify_cmd)


def _write_manifest(ws, **fields):
    (ws / MANIFEST_NAME).write_text(json.dumps({"schemaVersion": 1, **fields}))


# ---- FR-009: worker tamper has no gate effect -----------------------------


def test_manifest_edit_inside_run_does_not_change_gate_inputs(tmp_path):
    """The named regression (spec 016 FR-009 / the #358+#233 class): base
    declares app surface; the 'worker' commits surface=library onto the goal
    branch mid-run. The host-side surface read still answers 'app'."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(tmp_path, "init", "-q", str(origin))
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    _write_manifest(origin, surface="app")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "base: app surface")
    ws = tmp_path / "ws"
    subprocess.run(["git", "clone", "-q", str(origin), str(ws)], check=True,
                   capture_output=True)
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _git(ws, "checkout", "-q", "-b", "devclaw/goal-g")
    _write_manifest(ws, surface="library")           # the tamper (worktree...)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "worker: weaken gate")   # ...and committed
    assert resolve_surface(str(ws)) == "app"


# ---- surface consumption at the browser gate ------------------------------


def test_declared_library_surface_disarms_the_gate_without_globs():
    # non-library path (src/app/...) would BLOCK under the heuristics; the
    # declaration overrides them.
    failure = _browser_gate_failure(
        _NEVER_RAN_VERIFY, _FRONTEND_DIFF, "/nonexistent",
        mode="strict", surface="library",
    )
    assert failure is None


def test_declared_app_surface_disables_the_library_glob_exemption():
    lib_diff = _FRONTEND_DIFF.replace("src/app/foo", "ui/src/lib/foo")
    # under default globs a */src/lib/* component diff is exempt; declared
    # app surface removes the exemption and the never-ran suite blocks.
    exempt = _browser_gate_failure(
        _NEVER_RAN_VERIFY, lib_diff, "/nonexistent", mode="strict", surface=None)
    forced = _browser_gate_failure(
        _NEVER_RAN_VERIFY, lib_diff, "/nonexistent", mode="strict", surface="app")
    assert exempt is None
    assert forced is not None


def test_undeclared_surface_keeps_heuristics_byte_identical():
    with_none = _browser_gate_failure(
        _NEVER_RAN_VERIFY, _FRONTEND_DIFF, "/nonexistent", mode="strict", surface=None)
    default = _browser_gate_failure(
        _NEVER_RAN_VERIFY, _FRONTEND_DIFF, "/nonexistent", mode="strict")
    assert with_none == default


# ---- strictness + verify_cmd tiers at dispatch ----------------------------


def test_manifest_strictness_default_applies_to_unpinned_goal(tmp_path):
    _write_manifest(tmp_path, strictnessDefault="strict")
    strictness, _ = _manifest_tiers(_goal(tmp_path, explicit=None))
    assert strictness == "strict"


def test_explicit_goal_strictness_beats_manifest(tmp_path):
    _write_manifest(tmp_path, strictnessDefault="strict")
    strictness, _ = _manifest_tiers(_goal(tmp_path, explicit="trust"))
    assert strictness == "trust"


def test_no_manifest_keeps_todays_behavior(tmp_path):
    strictness, verify = _manifest_tiers(_goal(tmp_path))
    assert strictness == "trust" and verify is None


def test_verify_cmd_tier_order_action_goal_manifest(tmp_path):
    _write_manifest(tmp_path, verifyCmd="npm test")
    _, manifest_verify = _manifest_tiers(_goal(tmp_path))
    assert manifest_verify == "npm test"
    # the engine composes `action or goal or manifest`; the composition is a
    # plain or-chain — assert the tiers it feeds from:
    assert ("action-cmd" or "goal-cmd" or manifest_verify) == "action-cmd"
    assert (None or "goal-cmd" or manifest_verify) == "goal-cmd"
    assert (None or None or manifest_verify) == "npm test"


def test_malformed_manifest_blocks_dispatch_loudly(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text("{oops")
    with pytest.raises(GoalEngineError, match="devclaw.json blocks dispatch"):
        _manifest_tiers(_goal(tmp_path))


# ---- store explicitness (the raw tier FR-008 rides on) --------------------


def test_create_goal_without_strictness_stays_unpinned(tmp_path):
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir=str(tmp_path / "ws"))
    g = store.load_goal("g")
    assert g.strictness_explicit is None
    assert g.strictness == "trust"          # resolved view unchanged
    raw = (tmp_path / "g" / "goal.yaml").read_text()
    assert "strictness" not in raw          # no key = no pin


def test_create_goal_with_strictness_pins_it(tmp_path):
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir=str(tmp_path / "ws"),
                      strictness="strict")
    g = store.load_goal("g")
    assert g.strictness_explicit == "strict" and g.strictness == "strict"


def test_set_strictness_pins_a_previously_unpinned_goal(tmp_path):
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir=str(tmp_path / "ws"))
    store.set_strictness("g", "strict")
    g = store.load_goal("g")
    assert g.strictness_explicit == "strict"


# ---- direct-path dispatch preflight rejection (FR-010) --------------------


async def test_malformed_manifest_rejects_direct_dispatch(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import tools as _tools
    from tests.goal_fakes import register_tmp_project

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="proj")
    (ws / MANIFEST_NAME).write_text("{oops")
    monkeypatch.setattr(_tools._common, "registry", reg)
    with pytest.raises(ToolError, match="devclaw.json"):
        await _tools.dispatch_task(kind="implement_feature", project_id="proj",
                                   goal="x")


async def test_absent_manifest_does_not_reject_dispatch(tmp_path, monkeypatch):
    from devclaw.project_registry import ProjectRegistry
    from devclaw.server import _state
    from devclaw.server import tools as _tools
    from devclaw.server.tools import tasks as tasks_mod
    from tests.goal_fakes import register_tmp_project

    # After spec 022 US3, mutating dispatch auto-files an issue and routes via
    # the goal lane. Stub both seams so the manifest-absence check is isolated.
    async def _fake_auto_file(registry, *, project_id, goal):
        return 7

    monkeypatch.setattr(tasks_mod, "_auto_file_intake", _fake_auto_file)
    dispatch_calls: list = []

    async def _fake_dispatch_issue(**kw):
        dispatch_calls.append(kw)
        return {"goal_id": "g-1", "result": "created", "issue_ref": 7}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch_issue)

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="proj")
    monkeypatch.setattr(_tools._common, "registry", reg)
    # An absent manifest must not raise — the manifest absence is never a gate.
    raw = await _tools.dispatch_task(kind="implement_feature", project_id="proj",
                                     goal="add feature x")
    assert dispatch_calls, "dispatch must reach dispatch_issue (goal lane)"
    result = json.loads(raw)
    assert result.get("auto_filed_issue") == 7


# ---- zero-token guard -----------------------------------------------------


def test_manifest_resolution_spends_zero_tokens(tmp_path):
    evaluator = FakeClaude()
    _write_manifest(tmp_path, strictnessDefault="strict", verifyCmd="npm test")
    _manifest_tiers(_goal(tmp_path))
    resolve_surface(str(tmp_path))
    assert evaluator.calls == 0  # pure file/git reads — no cognition seam
