"""Three practices gate the code path (#1053, spec 048): a test writer that must
leave the tests RED before any code, a code pass that may not touch those
tests, and a one-line mutation per critical criterion that must turn the
checks red. Each is a fail-closed gate on an unattended loop; S-size goals
skip all three and cost no more than the baseline.

Real git and a real ``.devclaw/verify``; only the session (``run_turn``), the
pipeline init and the hand-off are stubbed.
"""

import os
import subprocess

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine
from devclaw.engine import practices

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
VERIFY = "#!/bin/sh\nset -e\nfor f in test_*.py; do python3 \"$f\"; done\n"
CRITICAL = "## Done when\n\n1. (critical) A person of 18 or over is an adult.\n"
PLAIN = "## Done when\n\n1. A person of 18 or over is an adult.\n"
FIXED = "def is_adult(age):\n    return age >= 18\n"
STRONG_TEST = "from calc import is_adult\nassert is_adult(18)\nassert not is_adult(17)\n"
WEAK_TEST = "from calc import is_adult\nassert is_adult(30)\nassert not is_adult(3)\n"
MUTATE = ('```json\n{"mutations": [{"criterion": 1, "file": "calc.py", "find": "    return age >= 18", '
          '"replace": "    return age > 18"}]}\n```\n')


def _git(cwd, *argv):
    return subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def _repo(tmp_path, *, verify=True):
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "goal/g1")
    (ws / "calc.py").write_text("def is_adult(age):\n    return False\n")
    if verify:
        (ws / ".devclaw").mkdir()
        (ws / ".devclaw" / "verify").write_text(VERIFY)
        (ws / ".devclaw" / "verify").chmod(0o755)
        (ws / ".devclaw" / "workflow.md").write_text("plan: none\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "base")
    _git(ws, "update-ref", "refs/remotes/origin/main", "HEAD")
    return ws


def _write(ws, name, text):
    (ws / name).write_text(text)


def _commit(ws):
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "--allow-empty", "-m", "work")


@pytest.fixture
def session(monkeypatch):
    """``script`` is a list of ``fn(ws) -> text``, one per turn; prompts land in ``prompts``."""
    state = {"script": [], "prompts": [], "events": [], "resumes": []}

    async def fake_turn(req, prompt, *, resume):
        state["prompts"].append(prompt)
        state["resumes"].append(resume)
        step = state["script"].pop(0) if state["script"] else (lambda ws: "DELIVERED: done")
        return engine.Turn(session_id="s1", ok=True, text=step(req.workspace_dir) or "DELIVERED: done",
                           usage={"input_tokens": 100, "output_tokens": 10})

    async def fake_init(ws):
        return None

    async def fake_hand_off(req, session_id, head):
        state["prompts"].append("<hand-off>")
        return engine.Turn(session_id=session_id, ok=True, text="DELIVERED: shipped",
                           usage={"input_tokens": 100, "output_tokens": 10}), "https://x/pull/1", ""

    monkeypatch.setattr(engine, "run_turn", fake_turn)
    monkeypatch.setattr(engine, "_init_pipeline", fake_init)
    monkeypatch.setattr(engine, "_hand_off", fake_hand_off)
    return state


def _req(ws, state, brief, size="M", resume=None):
    return EngineRequest(kind="implement_feature", workspace_dir=str(ws), goal=brief, hand_off=True,
                         closes=(1,), size=size, on_event=lambda e: state["events"].append(e),
                         resume_session_id=resume)


def _types(state):
    return [e.type for e in state["events"]]


async def test_the_tests_are_written_red_first_recorded_and_the_code_pass_makes_them_pass(session, tmp_path):
    ws = _repo(tmp_path)
    session["script"] = [
        lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: adulthood",
        lambda w: (_write(ws, "calc.py", FIXED), _commit(ws), MUTATE + "DELIVERED: adulthood")[-1],
    ]
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["status"] == "ok", result
    test_pass = next(e for e in session["events"] if e.type == "TestPass")
    assert test_pass.payload["red"] is True and test_pass.payload["files"] == ["test_adult.py"]
    recorded = await practices.recorded_test_pass(str(ws))
    assert recorded is not None and recorded.files == ("test_adult.py", ".devclaw/verify")
    # the code pass is a second, fresh session told which files are frozen
    assert "TEST PASS" in session["prompts"][0] and "TEST PASS" not in session["prompts"][1]
    assert "`test_adult.py`" in session["prompts"][1]
    assert next(e for e in session["events"] if e.type == "MutationCheck").payload["failures"] == []


async def test_tests_that_pass_before_any_code_stop_the_run_with_that_reason(session, tmp_path):
    ws = _repo(tmp_path)
    session["script"] = [lambda w: _write(ws, "test_adult.py", "assert True\n") or "TESTS: nothing"]
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["status"] == "blocked"
    assert "pass before any code is written" in result["reason"]
    assert len(session["prompts"]) == 1  # no code pass ran
    assert await practices.recorded_test_pass(str(ws)) is None


@pytest.mark.parametrize("touch, name", [
    (lambda ws: _write(ws, "test_adult.py", "assert True\n"), "edited `test_adult.py`"),
    (lambda ws: ((ws / "test_adult.py").unlink(missing_ok=True), _write(ws, "test_new.py", "assert True\n")),
     "deleted `test_adult.py`"),
    # skipping the frozen tests by rewriting what runs them
    (lambda ws: _write(ws, ".devclaw/verify", "#!/bin/sh\nexit 0\n"), "edited `.devclaw/verify`"),
])
async def test_a_code_pass_that_touches_a_test_pass_file_fails_the_gate_naming_it(session, tmp_path, touch, name):
    ws = _repo(tmp_path)

    def cheat(w):
        _write(ws, "calc.py", FIXED)
        touch(ws)
        _commit(ws)
        return MUTATE + "DELIVERED: done"

    session["script"] = [lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: t"] + [cheat] * 9
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["verify"]["passed"] is False and name in result["verify"]["output"]
    assert "<hand-off>" not in session["prompts"]


async def test_a_new_test_file_from_the_code_pass_is_allowed(session, tmp_path):
    ws = _repo(tmp_path)

    def code(w):
        _write(ws, "calc.py", FIXED)
        _write(ws, "test_more.py", "from calc import is_adult\nassert is_adult(40)\n")
        _commit(ws)
        return "DELIVERED: done"

    session["script"] = [lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: t", code]
    result = await engine.run_claude(_req(ws, session, PLAIN))
    assert result["status"] == "ok" and result["verify"]["passed"] is True
    assert "MutationCheck" not in _types(session)  # no critical criterion: the check is skipped


async def test_a_mutation_every_test_survives_fails_the_gate_naming_the_criterion(session, tmp_path):
    ws = _repo(tmp_path)

    def code(w):
        _write(ws, "calc.py", FIXED)
        _commit(ws)
        return MUTATE + "DELIVERED: done"

    session["script"] = [lambda w: _write(ws, "test_adult.py", WEAK_TEST) or "TESTS: t"] + [code] * 9
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    out = result["verify"]["output"]
    assert result["verify"]["passed"] is False and "critical criterion 1" in out and "18 or over" in out
    assert "left every test green" in out
    assert (ws / "calc.py").read_text() == FIXED  # the scratch copy took the mutation, never the tree


async def test_a_small_task_skips_every_stage_and_costs_no_more_than_the_baseline(session, tmp_path):
    """The recorded baseline: a session plus its hand-off — two turns. An S
    goal with a critical contract runs no test pass and no mutation, so it
    adds no turn."""
    baseline_turns = 2
    ws = _repo(tmp_path)

    def code(w):
        _write(ws, "calc.py", FIXED)
        _write(ws, "test_adult.py", STRONG_TEST)
        _commit(ws)
        return "DELIVERED: done"

    session["script"] = [code]
    result = await engine.run_claude(_req(ws, session, CRITICAL, size="S"))
    assert result["status"] == "ok"
    assert len(session["prompts"]) <= baseline_turns
    assert not {"TestPass", "MutationCheck"} & set(_types(session))
    assert "Task size: S" in session["prompts"][0]


async def test_a_test_pass_from_another_branch_is_not_this_goals(tmp_path):
    """A squash-merge copies an earlier goal's trailer onto the default branch."""
    ws = _repo(tmp_path)
    _git(ws, "commit", "-q", "--allow-empty", "-m",
         f"squash\n\n{practices.TEST_PASS_TRAILER}: goal/old red\n{practices.TEST_FILE_TRAILER}: test_x.py")
    assert await practices.recorded_test_pass(str(ws)) is None


@pytest.mark.parametrize("title, body, size", [
    ("Add Stripe billing", "## Done when\n- invoices go out", "L"),
    ("Tidy", "## Done when\n- rename in `devclaw/x.py`, no behaviour change", "S"),
    ("Tidy auth", "## Done when\n- rename in `devclaw/x.py`, no behaviour change", "L"),
    # an issue cannot declare its own size: only the written rules set it
    ("Tidy", "Size: S\n## Done when\n- tidy two files", "M"),
    ("Add a button", "## Done when\n- the console shows a button", "M"),
    ("Tidy", "## Done when\n- rename in `a.py` and `b.py`, no behaviour change", "M"),
])
def test_the_size_is_set_from_the_issue_by_the_written_rules(title, body, size):
    assert practices.size_of([(title, body)]) == size


async def test_a_branch_already_carrying_work_skips_the_test_pass_instead_of_blocking(session, tmp_path):
    """A goal in flight when the practices shipped: its code already passes the
    contract, so a test pass would be green and block on every wake."""
    ws = _repo(tmp_path)
    _write(ws, "calc.py", FIXED)
    _write(ws, "test_adult.py", STRONG_TEST)
    _commit(ws)
    session["script"] = [lambda w: MUTATE + "DELIVERED: done"]
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["status"] == "ok", result
    assert "TestPass" not in _types(session) and "TEST PASS" not in session["prompts"][0]


async def test_a_quota_paused_test_writer_restarts_as_a_test_writer_not_the_engineer(session, tmp_path):
    ws = _repo(tmp_path)
    _write(ws, "test_adult.py", STRONG_TEST)  # the paused writer's partial tests, kept in the tree
    session["script"] = [
        lambda w: "TESTS: adulthood",
        lambda w: (_write(ws, "calc.py", FIXED), _commit(ws), MUTATE + "DELIVERED: adulthood")[-1],
    ]
    result = await engine.run_claude(_req(ws, session, CRITICAL, resume="writer-sid"))
    assert result["status"] == "ok", result
    assert "TEST PASS" in session["prompts"][0] and session["resumes"][:2] == [None, None]
    recorded = await practices.recorded_test_pass(str(ws))
    assert recorded is not None and "test_adult.py" in recorded.files


async def test_the_mutations_block_carries_across_turns_of_one_run(session, tmp_path):
    """Turn 1 names the mutation but verify is red; turn 2 fixes it and hands
    back without repeating the block — the check still has the mutation."""
    ws = _repo(tmp_path)
    session["script"] = [
        lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: t",
        lambda w: MUTATE + "DELIVERED: not yet",
        lambda w: (_write(ws, "calc.py", FIXED), _commit(ws), "DELIVERED: done")[-1],
    ]
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["status"] == "ok", result
    assert next(e for e in session["events"] if e.type == "MutationCheck").payload["failures"] == []


@pytest.mark.parametrize("target", ["test_more.py", "tests/more.py", "more_test.py", "more.spec.py"])
async def test_a_mutation_of_a_test_file_is_refused(session, tmp_path, target):
    ws = _repo(tmp_path)
    mutate = ('```json\n{"mutations": [{"criterion": 1, "file": "%s", "find": "assert is_adult(40)", '
              '"replace": "assert not is_adult(40)"}]}\n```\n' % target)

    def code(w):
        _write(ws, "calc.py", FIXED)
        (ws / target).parent.mkdir(exist_ok=True)
        _write(ws, target, "from calc import is_adult\nassert is_adult(40)\n")
        _commit(ws)
        return mutate + "DELIVERED: done"

    session["script"] = [lambda w: _write(ws, "test_adult.py", WEAK_TEST) or "TESTS: t"] + [code] * 9
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["verify"]["passed"] is False and f"`{target}` is a test file" in result["verify"]["output"]


async def test_a_mutation_check_that_cannot_run_here_is_shown_and_not_charged(session, tmp_path):
    """Verify green in the real tree but red in the scratch worktree unmutated
    (it needs an ignored local file) — the check could not run here."""
    ws = _repo(tmp_path)
    _write(ws, ".gitignore", ".local\n")
    (ws / ".devclaw" / "verify").write_text(VERIFY + "test -f .local\n")
    _commit(ws)
    _git(ws, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(ws, ".local", "x")

    def code(w):
        _write(ws, "calc.py", FIXED)
        _commit(ws)
        return MUTATE + "DELIVERED: done"

    session["script"] = [lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: t", code]
    result = await engine.run_claude(_req(ws, session, CRITICAL))
    assert result["status"] == "ok" and result["verify"]["passed"] is True, result
    check = next(e for e in session["events"] if e.type == "MutationCheck").payload
    assert check["failures"] == [] and "red in the scratch worktree" in check["couldNotRun"]
    assert len(_git(ws, "worktree", "list").splitlines()) == 1  # the scratch worktree is gone


async def test_a_changed_contract_reruns_the_test_pass_and_refreezes(session, tmp_path):
    ws = _repo(tmp_path)
    session["script"] = [
        lambda w: _write(ws, "test_adult.py", STRONG_TEST) or "TESTS: t",
        lambda w: (_write(ws, "calc.py", FIXED), _commit(ws), "DELIVERED: done")[-1],
    ]
    assert (await engine.run_claude(_req(ws, session, PLAIN)))["status"] == "ok"
    changed = "## Done when\n\n1. A person of 21 or over is an adult.\n"
    session["prompts"].clear()
    session["script"] = [
        # the writer revises its own frozen test: allowed, it is not the code pass
        lambda w: _write(ws, "test_adult.py", STRONG_TEST.replace("18", "21").replace("17", "20")) or "TESTS: t",
        lambda w: (_write(ws, "calc.py", FIXED.replace("18", "21")), _commit(ws), "DELIVERED: done")[-1],
    ]
    result = await engine.run_claude(_req(ws, session, changed))
    assert result["status"] == "ok", result
    assert "TEST PASS" in session["prompts"][0] and "contract changed" in session["prompts"][0]
    recorded = await practices.recorded_test_pass(str(ws))
    assert recorded.contract == practices.contract_hash(practices.contract_criteria(changed))


async def test_a_frozen_file_change_merged_in_from_the_default_branch_is_not_the_code_pass(tmp_path):
    ws = _repo(tmp_path)
    lines = [f"# line {n}" for n in range(10)]
    _write(ws, "test_adult.py", "\n".join(lines) + "\n")
    _commit(ws)
    _git(ws, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(ws, "test_adult.py", "\n".join(lines) + "\n" + STRONG_TEST)
    assert await practices.commit_test_pass(str(ws), ["test_adult.py"], {"exit_code": 1}, "h")
    record = await practices.recorded_test_pass(str(ws))
    # the default branch edits the same test file; the goal merges it in
    _git(ws, "checkout", "-q", "-b", "main", "origin/main")
    _write(ws, "test_adult.py", "\n".join(["# main"] + lines[1:]) + "\n")
    _commit(ws)
    _git(ws, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(ws, "checkout", "-q", "goal/g1")
    _git(ws, "merge", "-q", "--no-edit", "origin/main")
    assert await practices.frozen_violations(str(ws), record.commit, record.files) == []
    _write(ws, "test_adult.py", (ws / "test_adult.py").read_text() + "assert True\n")
    assert await practices.frozen_violations(str(ws), record.commit, record.files) == ["edited `test_adult.py`"]
