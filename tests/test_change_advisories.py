"""The relocated post-run advisories, reading the ONE span (spec 013 US3).

These three checks used to run inside the sandbox against
``git diff <.devclaw-pre-head>`` — a third independent computation of "what did
the agent change?", with the same blindness as the gates. On 2026-08-22 a run
that had just CREATED ``AGENTS.md`` reported *"AGENTS.md exists but was not
updated this run"* (#630). They read the materialized span now, host-side.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.engine import EngineRequest
from devclaw.quality.change_advisories import added_paths, change_advisories
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


def _added(path: str, lines: int = 1) -> str:
    body = "".join(f"+line{i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{lines} @@\n{body}"
    )


def _modified(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    )


# ---- the regression the issue names ---------------------------------------


def test_the_repo_guide_advisory_does_not_fire_on_a_run_that_created_the_guide(tmp_path):
    """US3 AS#2, the literal 2026-08-22 observation. The guide was CREATED by
    this run — as an unrecorded new file, invisible to the old in-sandbox
    check — and the run was told it had not been updated."""
    (tmp_path / "AGENTS.md").write_text("# guide\n")
    diff = _added("AGENTS.md", 41) + _modified("README.md")
    notes = change_advisories(diff, workspace_dir=str(tmp_path))
    assert not any("AGENTS.md exists but was not updated" in n for n in notes)


def test_the_repo_guide_advisory_still_fires_when_the_run_left_it_untouched(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# guide\n")
    notes = change_advisories(_modified("src/app.py"), workspace_dir=str(tmp_path))
    assert any("AGENTS.md exists but was not updated" in n for n in notes)


def test_no_repo_guide_advisory_when_the_repo_has_no_guide(tmp_path):
    notes = change_advisories(_modified("src/app.py"), workspace_dir=str(tmp_path))
    assert notes == []


def test_a_run_that_changed_nothing_gets_no_advisories(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# guide\n")
    assert change_advisories("", workspace_dir=str(tmp_path)) == []


# ---- the two browser advisories -------------------------------------------


def test_new_browser_specs_without_a_browser_verify_cmd_warn(tmp_path):
    notes = change_advisories(
        _added("e2e/smoke.spec.ts"), workspace_dir=str(tmp_path), verify_cmd="pytest -q",
    )
    joined = "\n".join(notes)
    assert "browser tests added but verify_cmd" in joined
    assert "e2e/smoke.spec.ts" in joined


def test_an_unrecorded_new_browser_spec_is_seen_too(tmp_path):
    """The point of the relocation: the check reads the materialized span, so a
    spec file the agent never recorded is in front of it like any other."""
    notes = change_advisories(
        _added("web/tests/checkout.spec.ts"),
        workspace_dir=str(tmp_path), verify_cmd="pytest -q",
    )
    assert any("checkout.spec.ts" in n for n in notes)


def test_browser_specs_are_silent_when_verify_cmd_runs_playwright(tmp_path):
    notes = change_advisories(
        _added("e2e/smoke.spec.ts"), workspace_dir=str(tmp_path),
        verify_cmd="npx playwright test --reporter=json",
    )
    assert not any("browser tests added" in n for n in notes)


def test_ui_source_without_a_browser_run_warns_that_the_gate_will_block(tmp_path):
    notes = change_advisories(
        _modified("web/src/app/foo.component.ts"),
        workspace_dir=str(tmp_path), verify_cmd="ng build && vitest run",
    )
    joined = "\n".join(notes)
    assert "web-UI source changed" in joined and "fail this CLOSED" in joined


def test_library_surface_is_exempt_from_the_ui_advisory(tmp_path):
    """A library-only slice wires nothing into a running app route, so the host
    browser gate exempts it — the nudge must match (cmn-tab-group, 2026-07-18)."""
    notes = change_advisories(
        _modified("libs/ui/src/lib/tab-group.component.ts"),
        workspace_dir=str(tmp_path), verify_cmd="ng build",
    )
    assert not any("web-UI source changed" in n for n in notes)


def test_backend_only_change_is_silent(tmp_path):
    notes = change_advisories(
        _modified("backend/Program.cs"), workspace_dir=str(tmp_path),
        verify_cmd="dotnet test",
    )
    assert notes == []


# ---- totality --------------------------------------------------------------


def test_added_paths_reads_creations_and_ignores_modifications():
    diff = _added("a.py") + _modified("b.py")
    assert added_paths(diff) == ("a.py",)


def test_an_advisory_crash_is_a_warning_line_never_an_exception(monkeypatch):
    import devclaw.quality.change_advisories as mod

    def _boom(_diff):
        raise ValueError("parser exploded")

    monkeypatch.setattr(mod, "changed_paths", _boom)
    notes = mod.change_advisories("anything", workspace_dir="/nope")
    assert notes and "could not run" in notes[0]


# ---- it lands where the hook's warnings used to ----------------------------


@pytest.mark.asyncio
async def test_the_advisories_ride_the_task_result_where_the_hook_warnings_did(tmp_path):
    store = StateStore(str(tmp_path / "t.db"))
    try:
        ws = tmp_path / "ws"
        ws.mkdir()
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)
        (ws / "AGENTS.md").write_text("# guide\n")
        subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "base"],
                       check=True, capture_output=True)

        async def runner(req: EngineRequest):
            (ws / "src.py").write_text("S = 1\n")  # never recorded by the agent
            return {"status": "ok", "workspaceDir": req.workspace_dir,
                    "verify": {"ran": True, "cmd": "pytest", "passed": True,
                               "exit_code": 0, "timed_out": False, "output": ""}}

        q = TaskQueue(store, runner=runner)
        tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                       verify_cmd="pytest")
        await q.drain()

        result = json.loads(store.get_task(tid).result_json)
        warnings = result.get("hook_warnings") or []
        assert any("[change-advisory]" in w and "AGENTS.md" in w for w in warnings)
    finally:
        store.close()
