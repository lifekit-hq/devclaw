"""The no-mistakes hand-off fails closed (#953).

The session ships its own branch through ``no-mistakes axi run``; the engine
never takes its word for it. A hand-off counts only when the branch's newest
run carries the session's head and passed — anything else is an error the
queue settles ``INTERRUPTED``, never an ``ok`` that would read as delivered.

All seams stubbed: no claude, no no-mistakes, no git. The ``axi status`` text
is the real v1.99.0 output captured in ``tests/fixtures/`` (the tool has no
machine-readable mode): ``-running`` is this repo's own parked run,
``-completed-other-branch`` a passed run queried with ``--run`` so its block is
``other_branch_run:``; the engine reads its own branch's ``run:`` block.
"""

from pathlib import Path

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "a" * 40
PR = "https://github.com/lifekit-hq/devclaw/pull/963"
RUNNING = (FIXTURES / "axi-status-v1.99.0-running.toon").read_text()
COMPLETED = (FIXTURES / "axi-status-v1.99.0-completed-other-branch.toon").read_text()


def _status(outcome: str, submitted: str = HEAD, pr: str = PR, tail: str = "") -> str:
    """The captured passed run as the engine's own branch reports it: its block
    renamed ``run:``, a ``branch_sync`` block added, the given values set."""
    text = COMPLETED.replace("other_branch_run:", "run:").replace(f'pr: "{PR}"', f'pr: "{pr}"')
    text = text.replace("outcome: passed", f"outcome: {outcome}").split("Shell cwd was reset")[0]
    return f"{text}branch_sync:\n  pipeline:\n    submitted_head: {submitted}\n{tail}"


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
    ({"status": (0, COMPLETED.split("Shell cwd was reset")[0])}, "carries"),
    ({"status": (0, RUNNING)}, "carries"),
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


def test_the_runs_own_fields_are_read_from_the_captured_status():
    assert engine.parse_status(RUNNING) == {"submitted_head": "a8dbb7bdbe2ad2054741fe1f859cd6603d4e002b"}
    assert engine.parse_status(_status("passed")) == {"outcome": "passed", "pr": PR, "submitted_head": HEAD}


async def test_a_step_or_nested_line_never_overrides_the_runs_own_value(stubbed, tmp_path):
    turns, state = stubbed
    nested = "    outcome: passed\n    pr: https://github.com/o/r/pull/1\n  outcome: passed\n"
    state["status"] = (0, _status("failed", tail=nested))
    parsed = engine.parse_status(state["status"][1])
    assert parsed["outcome"] == "failed" and parsed["pr"] == PR
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "ended failed" in result["error"]


async def test_uncommitted_work_with_nothing_to_ship_is_never_ok(stubbed, tmp_path, monkeypatch):
    turns, state = stubbed

    async def no_commit(workspace_dir):
        return None

    async def dirty(workspace_dir):
        return True

    monkeypatch.setattr(engine, "unshipped_head", no_commit)
    monkeypatch.setattr(engine, "dirty_tree", dirty)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "uncommitted" in result["error"]
    assert len(turns) == 1 and "pr_url" not in result

    async def clean(workspace_dir):
        return False

    monkeypatch.setattr(engine, "dirty_tree", clean)
    assert (await engine.run_claude(_req(tmp_path)))["status"] == "ok"


async def test_no_hand_off_flag_never_runs_no_mistakes(stubbed, tmp_path):
    turns, state = stubbed
    result = await engine.run_claude(_req(tmp_path, hand_off=False))
    assert result["status"] == "ok" and "pr_url" not in result and len(turns) == 1
