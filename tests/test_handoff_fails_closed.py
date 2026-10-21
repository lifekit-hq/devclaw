"""The no-mistakes hand-off fails closed (#953).

The session ships its own branch through ``no-mistakes axi run``; the engine
never takes its word for it. A hand-off counts only when the branch's newest
run carries the session's head and passed — anything else is an error the
queue settles ``INTERRUPTED``, never an ``ok`` that would read as delivered.

All seams stubbed: no claude, no no-mistakes, no git.
"""

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

HEAD = "a" * 40
PR = "https://github.com/o/r/pull/7"


def _status(outcome: str, submitted: str = HEAD, pr: str = PR) -> str:
    return (f'run:\n  id: "01X"\n  status: completed\n  pr: "{pr}"\n'
            f"branch_sync:\n  pipeline:\n    submitted_head: {submitted}\noutcome: {outcome}\n")


@pytest.fixture
def stubbed(monkeypatch):
    turns: list[str] = []
    seen: dict = {}

    async def fake_turn(req, prompt, *, resume):
        turns.append(prompt)
        return engine.Turn(session_id="s1", ok=True, text="DELIVERED: one increment")

    async def fake_gate(req, cmd):
        return {"ran": True, "cmd": cmd, "passed": True, "exit_code": 0, "timed_out": False, "output": ""}, None

    async def fake_unshipped(workspace_dir):
        return HEAD

    async def fake_host(workspace_dir, *argv, timeout=120.0):
        if argv[:2] == ("no-mistakes", "init"):
            return seen.get("init", (0, ""))
        if argv[:3] == ("no-mistakes", "axi", "status"):
            return seen["status"]
        if argv[:3] == ("git", "merge-base", "--is-ancestor"):
            return (0, "") if argv[4] == HEAD else (1, "")
        raise AssertionError(f"unexpected host command {argv}")

    monkeypatch.setattr(engine, "run_turn", fake_turn)
    monkeypatch.setattr(engine, "_gate", fake_gate)
    monkeypatch.setattr(engine, "unshipped_head", fake_unshipped)
    monkeypatch.setattr(engine, "_host", fake_host)
    return turns, seen


def _req(tmp_path, hand_off=True):
    return EngineRequest(kind="implement_feature", workspace_dir=str(tmp_path), goal="g",
                         verify_cmd="true", hand_off=hand_off, closes=(953,))


@pytest.mark.parametrize("seen, why", [
    ({"status": (0, _status("failed"))}, "ended failed"),
    ({"status": (0, _status("cancelled"))}, "ended cancelled"),
    ({"status": (0, _status("passed", submitted="b" * 40))}, "carries"),
    ({"status": (0, _status("passed", pr=""))}, "names no PR"),
    ({"status": (1, "daemon unreachable")}, "status failed"),
    ({"init": (1, "no origin remote"), "status": (0, _status("passed"))}, "init failed"),
])
async def test_a_hand_off_without_a_passed_run_carrying_the_head_is_never_ok(stubbed, tmp_path, seen, why):
    turns, state = stubbed
    state.update(seen)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and why in result["error"]
    assert "pr_url" not in result


async def test_a_passed_run_carrying_the_head_ships_with_its_pr(stubbed, tmp_path):
    turns, state = stubbed
    state["status"] = (0, _status("passed-with-skips"))
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "ok" and result["pr_url"] == PR
    assert "--skip ci --wait 100s --closes 953" in turns[-1]


async def test_no_hand_off_flag_never_runs_no_mistakes(stubbed, tmp_path):
    turns, state = stubbed
    result = await engine.run_claude(_req(tmp_path, hand_off=False))
    assert result["status"] == "ok" and "pr_url" not in result and len(turns) == 1
