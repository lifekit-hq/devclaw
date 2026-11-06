"""The no-mistakes hand-off fails closed (#953).

The session ships its own branch through ``no-mistakes axi run``; the engine
never takes its word for it. A hand-off counts only when the branch's newest
run carries the session's head and passed — anything else is an error the
queue settles ``INTERRUPTED``, never an ``ok`` that would read as delivered.

All seams stubbed: no claude, no no-mistakes. The ``axi status`` text is real
v1.99.0 output captured in ``tests/fixtures/``, read unmodified (the tool has
no machine-readable mode):

- ``-running``: this repo's own parked run, pre-push — the only moment
  ``branch_sync`` (and its ``submitted_head``) is printed.
- ``-completed-other-branch``: a passed run queried with ``--run``, so its
  block is ``other_branch_run:``.
- ``-finished``: the engine's own branch after a passed run — ``run:`` and
  ``outcome: passed``, a PR, no ``branch_sync`` (#984).
- ``-running-mid-pipeline``: the engine's own branch, a run that already carries
  its PR, step still running, no ``outcome``, no ``branch_sync``. A pushed run
  whose ``ci`` step is still running could not be captured; this stands in.
- ``-cancelled`` (``outcome: cancelled``, ``ci`` failed) and ``-failed-push``
  (``outcome: failed``, no PR).

Once a run has pushed or finished, ``run.head_sha`` is the only head left; it is
read from the gate repo after a fetch.
"""

import os
import subprocess
from pathlib import Path

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / f"axi-status-v1.99.0-{name}.toon").read_text()


RUNNING = _fixture("running")
COMPLETED = _fixture("completed-other-branch")
FINISHED = _fixture("finished")
MID_PIPELINE = _fixture("running-mid-pipeline")
CANCELLED = _fixture("cancelled")
FAILED_PUSH = _fixture("failed-push")

HEAD = "a" * 40
SUBMITTED = "a8dbb7bdbe2ad2054741fe1f859cd6603d4e002b"
FINISHED_HEAD = "983aecf7b333978b467d65acebe9e93dadb1ebff"
CANCELLED_HEAD = "a2e7b692223f949f6f10f2ed11c845ed5b078c40"
FAILED_PUSH_HEAD = "6e553b1f19527d754ae9d5328f4742790cda2421"
#: the session's HEAD is an ancestor of exactly these heads
DESCENDANTS = {SUBMITTED, FINISHED_HEAD, CANCELLED_HEAD, FAILED_PUSH_HEAD}
PR = "https://github.com/lifekit-hq/devclaw/pull/983"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


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
        return seen.get("head", HEAD)

    async def fake_host(workspace_dir, *argv, timeout=120.0):
        seen.setdefault("calls", []).append(argv[:2])
        if argv[:2] == ("no-mistakes", "init"):
            return seen.get("init", (0, ""))
        if argv[:3] == ("no-mistakes", "axi", "status"):
            return seen["status"]
        if argv[:3] == ("git", "merge-base", "--is-ancestor"):
            return (0, "") if argv[3] == HEAD and argv[4] in DESCENDANTS else (1, "")
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
    ({"status": (0, FAILED_PUSH)}, "ended failed"),
    ({"status": (0, CANCELLED)}, "ended cancelled"),
    ({"status": (0, MID_PIPELINE)}, "without an outcome"),
    ({"status": (0, RUNNING)}, "without an outcome"),
    ({"status": (0, FINISHED), "head": "b" * 40}, "carries"),
    ({"status": (0, COMPLETED)}, "carries"),
    ({"status": (1, "daemon unreachable")}, "status failed"),
    ({"init": (1, "no origin remote"), "status": (0, FINISHED)}, "init failed"),
])
async def test_a_hand_off_without_a_passed_run_carrying_the_head_is_never_ok(stubbed, tmp_path, seen, why):
    turns, state = stubbed
    state.update(seen)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and why in result["error"]
    assert "pr_url" not in result


async def test_a_finished_run_carrying_the_head_ships_with_its_pr(stubbed, tmp_path):
    """#984: a finished run prints no ``branch_sync``; its ``head_sha`` (fetched
    from the gate repo) still descends from the session's."""
    turns, state = stubbed
    state["status"] = (0, FINISHED)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "ok" and result["pr_url"] == PR
    assert "--skip ci --wait 100s --closes 953" in turns[-1]
    assert ("git", "fetch") in state["calls"]


async def test_a_pre_push_run_is_matched_on_its_submitted_head_without_a_fetch(stubbed, tmp_path):
    turns, state = stubbed
    state["status"] = (0, RUNNING)
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "without an outcome" in result["error"]
    assert ("git", "fetch") not in state["calls"]


async def test_the_shipped_check_fails_closed_on_real_git_ancestry(tmp_path, monkeypatch):
    """The run's head must descend from the session's head in a real object
    store; the pipeline's commits live in the gate repo until ``git fetch
    no-mistakes`` brings them, and a head that never arrives (or is not a
    descendant) is not shipped. The remote is a real bare repo named
    ``no-mistakes`` with the fetch refspec the tool configures. Git needs real
    objects, so the one substitution is the ``head_sha`` of the captured
    finished status: the commit this test creates stands in for ``FINISHED_HEAD``."""
    def git(cwd, *argv):
        return subprocess.run(["git", *argv], cwd=cwd, check=True, text=True,
                              capture_output=True, env={**os.environ, **GIT_ENV}).stdout.strip()

    work, gate, pipeline = tmp_path / "work", tmp_path / "gate.git", tmp_path / "pipeline"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "commit", "-q", "--allow-empty", "-m", "session")
    head = git(work, "rev-parse", "HEAD")
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(gate))
    git(work, "push", "-q", str(gate), "main")
    git(tmp_path, "clone", "-q", str(gate), str(pipeline))
    git(pipeline, "commit", "-q", "--allow-empty", "-m", "no-mistakes(review): fix")
    run_head = git(pipeline, "rev-parse", "HEAD")
    git(pipeline, "push", "-q", "origin", "HEAD:refs/heads/goal")
    git(pipeline, "checkout", "-q", "--orphan", "other")
    git(pipeline, "commit", "-q", "--allow-empty", "-m", "unrelated")
    unrelated = git(pipeline, "rev-parse", "HEAD")
    git(pipeline, "push", "-q", "origin", "HEAD:refs/heads/other")
    git(work, "remote", "add", "no-mistakes", str(tmp_path / "missing.git"))
    git(work, "config", "remote.no-mistakes.fetch", "+refs/heads/*:refs/remotes/no-mistakes/*")
    real_host = engine._host

    async def host(workspace_dir, *argv, timeout=120.0):
        if argv[:3] == ("no-mistakes", "axi", "status"):
            return 0, FINISHED.replace(FINISHED_HEAD, host.run_head)
        return await real_host(workspace_dir, *argv, timeout=timeout)

    monkeypatch.setattr(engine, "_host", host)
    host.run_head = run_head
    assert await engine.shipped_run(str(work), head) == (None, f"no no-mistakes run carries {head[:8]}")
    git(work, "remote", "set-url", "no-mistakes", str(gate))
    assert await engine.shipped_run(str(work), head) == (PR, "")
    host.run_head = unrelated
    assert (await engine.shipped_run(str(work), head))[0] is None
    host.run_head = "d" * 40
    assert (await engine.shipped_run(str(work), head))[0] is None


def test_the_runs_own_fields_are_read_from_the_captured_status():
    assert engine.parse_status(RUNNING) == {"run_head": "b786039d5c4ce58f628769fc53fd7f03f04b121a",
                                            "submitted_head": "a8dbb7bdbe2ad2054741fe1f859cd6603d4e002b"}
    assert engine.parse_status(FINISHED) == {"outcome": "passed", "pr": PR, "run_head": FINISHED_HEAD}
    assert engine.parse_status(COMPLETED) == {"outcome": "passed"}  # another branch's run never reads as ours


async def test_a_step_or_nested_line_never_overrides_the_runs_own_value(stubbed, tmp_path):
    turns, state = stubbed
    nested = "    outcome: passed\n    pr: https://github.com/o/r/pull/1\n  outcome: passed\n"
    state["status"] = (0, CANCELLED + nested)
    parsed = engine.parse_status(state["status"][1])
    assert parsed["outcome"] == "cancelled" and parsed["pr"] == PR
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "ended cancelled" in result["error"]


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
    state["status"] = (0, FINISHED)
    assert (await engine.run_claude(_req(tmp_path)))["status"] == "ok"
    assert state["calls"][:2] == [("no-mistakes", "init"), ("turn", 1)]

    turns.clear()
    state["init"] = (1, "daemon unreachable")
    result = await engine.run_claude(_req(tmp_path))
    assert result["status"] == "error" and "init failed" in result["error"]
    assert turns == []
