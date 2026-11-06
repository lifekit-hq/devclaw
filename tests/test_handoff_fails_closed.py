"""The no-mistakes hand-off fails closed (#953).

The session ships its own branch through ``no-mistakes axi run``; the engine
never takes its word for it. A hand-off counts only when the branch's newest
run carries the session's head and passed — anything else is an error the
queue settles ``INTERRUPTED``, never an ``ok`` that would read as delivered.

All seams stubbed: no claude, no no-mistakes, no git. The ``axi status`` text
is the real v1.99.0 output captured in ``tests/fixtures/`` (the tool has no
machine-readable mode): ``-running`` is this repo's own parked run (pre-push: the only moment
``branch_sync`` is printed), ``-completed-other-branch`` a passed run queried
with ``--run`` so its block is ``other_branch_run:``. Once a run has pushed or
finished, plain ``axi status`` prints no ``branch_sync`` (#984): the engine's own
branch reports ``run:`` and ``outcome:`` only, so ``_status`` is the captured
passed run with its block renamed ``run:`` and nothing added. The run's
``head_sha`` is the only head left, read from the gate repo after a fetch.
"""

import os
import subprocess
from pathlib import Path

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "a" * 40
#: the head the pipeline finished at: HEAD plus the review/document fix commits
RUN_HEAD = "c" * 40
PR = "https://github.com/lifekit-hq/devclaw/pull/963"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
RUNNING = (FIXTURES / "axi-status-v1.99.0-running.toon").read_text()
COMPLETED = (FIXTURES / "axi-status-v1.99.0-completed-other-branch.toon").read_text()


def _status(outcome: str, head: str = RUN_HEAD, pr: str = PR, tail: str = "") -> str:
    """The captured passed run as the engine's own branch reports it once it has
    finished: its block renamed ``run:``, the given values set, no ``branch_sync``."""
    text = COMPLETED.replace("other_branch_run:", "run:").replace(f'pr: "{PR}"', f'pr: "{pr}"')
    text = text.replace("95d645369cf8dc6896fc5efc4d0e725d5eb48f33", head)
    text = text.replace("outcome: passed", f"outcome: {outcome}").split("Shell cwd was reset")[0]
    return f"{text}{tail}"


def _pushed(outcome: str = "") -> str:
    """The same run after its push, while the ``ci`` step still watches the PR:
    the finished status with that step running (and the outcome, once it has one)."""
    text = _status("passed").replace("status: completed", "status: running", 1)
    text = text.replace("ci,completed,0,438511", "ci,running,0,0").replace("outcome: passed\n", "")
    return text + (f"outcome: {outcome}\n" if outcome else "")


@pytest.fixture
def stubbed(monkeypatch):
    turns: list[str] = []
    seen: dict = {}

    async def fake_turn(req, prompt, *, resume):
        turns.append(prompt)
        seen.setdefault("calls", []).append(("turn", len(turns)))
        return engine.Turn(session_id="s1", ok=True, text="DELIVERED: one increment")

    async def fake_gate(req, cmd):
        return {"ran": True, "cmd": cmd, "passed": True, "exit_code": 0, "timed_out": False, "output": ""}, None

    async def fake_unshipped(workspace_dir):
        return HEAD

    async def fake_host(workspace_dir, *argv, timeout=120.0):
        seen.setdefault("calls", []).append(argv[:2])
        if argv[:2] == ("no-mistakes", "init"):
            return seen.get("init", (0, ""))
        if argv[:3] == ("no-mistakes", "axi", "status"):
            return seen["status"]
        if argv[:3] == ("git", "merge-base", "--is-ancestor"):
            return (0, "") if argv[3] == HEAD and argv[4] in {HEAD, RUN_HEAD} else (1, "")
        if argv[:3] == ("git", "fetch", "no-mistakes"):
            return seen.get("fetch", (0, ""))
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
    ({"status": (0, _status("passed", head="b" * 40))}, "carries"),
    ({"status": (0, _pushed())}, "without an outcome"),
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


@pytest.mark.parametrize("status", [
    _status("passed"),
    _status("passed-with-skips"),
    _pushed("checks-passed"),
])
async def test_a_pushed_or_finished_run_carrying_the_head_ships_with_its_pr(stubbed, tmp_path, status):
    """#984: a run that has pushed or finished prints no ``branch_sync``; its
    ``head_sha`` (fetched from the gate repo) still descends from the session's."""
    turns, state = stubbed
    state["status"] = (0, status)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "ok" and result["pr_url"] == PR
    assert "--skip ci --wait 100s --closes 953" in turns[-1]
    assert ("git", "fetch") in state["calls"]


async def test_a_pre_push_run_is_matched_on_its_submitted_head_without_a_fetch(stubbed, tmp_path):
    turns, state = stubbed
    state["status"] = (0, RUNNING.replace("a8dbb7bdbe2ad2054741fe1f859cd6603d4e002b", HEAD) + "outcome: passed\n")
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "names no PR" in result["error"]
    assert ("git", "fetch") not in state["calls"]


async def test_the_shipped_check_fails_closed_on_real_git_ancestry(tmp_path, monkeypatch):
    """The run's head must descend from the session's head in a real object
    store; the pipeline's commits are in the gate repo until the fetch brings
    them, and a head that never arrives (or is not a descendant) is not shipped."""
    def git(cwd, *argv):
        return subprocess.run(["git", *argv], cwd=cwd, check=True, text=True,
                              capture_output=True, env={**os.environ, **GIT_ENV}).stdout.strip()

    work, gate = tmp_path / "work", tmp_path / "gate"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "commit", "-q", "--allow-empty", "-m", "session")
    head = git(work, "rev-parse", "HEAD")
    subprocess.run(["git", "clone", "-q", str(work), str(gate)], check=True)
    git(gate, "commit", "-q", "--allow-empty", "-m", "no-mistakes(review): fix")
    run_head = git(gate, "rev-parse", "HEAD")
    git(gate, "checkout", "-q", "--orphan", "other")
    git(gate, "commit", "-q", "--allow-empty", "-m", "unrelated")
    unrelated = git(gate, "rev-parse", "HEAD")
    real_host = engine._host
    gate_reachable = {"yes": False}

    async def host(workspace_dir, *argv, timeout=120.0):
        if argv[:3] == ("no-mistakes", "axi", "status"):
            return 0, _status("passed", head=host.run_head)
        if argv[:3] == ("git", "fetch", "no-mistakes"):
            if not gate_reachable["yes"]:
                return 128, "fatal: 'no-mistakes' does not appear to be a git repository"
            return await real_host(workspace_dir, "git", "fetch", "-q", str(gate), "+refs/heads/*:refs/remotes/no-mistakes/*")
        return await real_host(workspace_dir, *argv, timeout=timeout)

    monkeypatch.setattr(engine, "_host", host)
    host.run_head = run_head
    assert await engine.shipped_run(str(work), head) == (None, f"no no-mistakes run carries {head[:8]}")
    gate_reachable["yes"] = True
    assert await engine.shipped_run(str(work), head) == (PR, "")
    host.run_head = unrelated
    assert (await engine.shipped_run(str(work), head))[0] is None
    host.run_head = "d" * 40
    assert (await engine.shipped_run(str(work), head))[0] is None


def test_the_runs_own_fields_are_read_from_the_captured_status():
    assert engine.parse_status(RUNNING) == {"run_head": "b786039d5c4ce58f628769fc53fd7f03f04b121a",
                                            "submitted_head": "a8dbb7bdbe2ad2054741fe1f859cd6603d4e002b"}
    assert engine.parse_status(_status("passed")) == {"outcome": "passed", "pr": PR, "run_head": RUN_HEAD}
    assert engine.parse_status(COMPLETED) == {"outcome": "passed"}  # another branch's run never reads as ours


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


async def test_the_pipeline_is_initialised_before_the_session_and_a_dead_one_spends_no_session(stubbed, tmp_path):
    """A session that finds no gate reads the hand-off as impossible and ends
    BLOCKED (2026-11-05, the first goal in the container). The host stands the
    pipeline up BEFORE the first turn; if it cannot, no session is spent."""
    turns, state = stubbed
    (tmp_path / ".git").mkdir()
    state["status"] = (0, _status("passed"))
    assert (await engine.run_claude(_req(tmp_path)))["status"] == "ok"
    assert state["calls"][:2] == [("no-mistakes", "init"), ("turn", 1)]

    turns.clear()
    state["init"] = (1, "daemon unreachable")
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "init failed" in result["error"]
    assert turns == []
