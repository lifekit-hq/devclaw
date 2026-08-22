"""Regression tests for the declared-scope GATE (spec 010 FR-103/FR-104/FR-105).

The substrate is unit-tested in ``test_declared_scope.py``; this module pins the
gate's place in the settle cascade:

  * a violation FAILS the increment — always-hard, in trust as well as strict;
  * an unreviewable check fails CLOSED (a crash is not an approval, #186);
  * an increment whose plan declared nothing is not consulted, so the chain is
    byte-identical to before this gate existed;
  * the gate runs BEFORE the cognition gates, so a hermeticity violation costs
    zero `claude` calls;
  * a spec directory claimed at RUNTIME is, by construction, outside every
    declared scope — which is how FR-104 is enforced rather than requested;
  * the worker has no surface from which to spawn a worker (FR-105);
  * the judged span is the WORKSPACE's, not the agent's bookkeeping — an
    out-of-scope file the agent never committed is still a violation (#630).

Unit tests — no docker, no claude; one test shells out to real git to pin the
completeness probe.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from devclaw import task_queue as tq
from devclaw.quality.gate_pipeline import GateInput, GateVerdict, run_pipeline
from devclaw.quality.gate_policy import ALWAYS_HARD, Consequence, gate_consequence

CLAIM_DIFF = (
    "diff --git a/specs/010-feat/tasks.md b/specs/010-feat/tasks.md\n"
    "--- a/specs/010-feat/tasks.md\n+++ b/specs/010-feat/tasks.md\n"
    "@@ -1 +1 @@\n"
    "-- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
    "+- [x] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
)


def _file_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1 @@\n+touched\n"
    )


class _RecordingCognitionGate:
    """Stand-in for the two gates that call `claude` — records whether it ran."""

    def __init__(self) -> None:
        self.gate_id = "review"
        self.calls = 0

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        self.calls += 1
        await gi.diff()
        return GateVerdict.passed(self.gate_id)


def _gate_input(diff: str, *, declared=(), diff_calls=None) -> GateInput:
    async def diff_fn() -> str:
        if diff_calls is not None:
            diff_calls.append(1)
        return diff

    return GateInput(
        kind="implement_feature",
        goal="g",
        workspace_dir="/ws",
        verify={"ran": True, "passed": True},
        scaffold=False,
        browser_mode="flexible",
        diff_fn=diff_fn,
        declared_scope=declared,
    )


@pytest.mark.asyncio
async def test_out_of_scope_increment_fails_the_gate_and_never_ships():
    gi = _gate_input(CLAIM_DIFF + _file_diff("src/widget/a.py") + _file_diff("infra/deploy.sh"))
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is False
    assert verdict.gate_id == "scope"
    assert "infra/deploy.sh" in (verdict.reason or "")
    # never dial-able: the finding must not be advised-and-shipped under trust
    assert verdict.dialable is False


@pytest.mark.asyncio
async def test_in_scope_increment_passes_the_gate_untouched():
    gi = _gate_input(CLAIM_DIFF + _file_diff("src/widget/a.py") + _file_diff("src/widget/deep/b.py"))
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is True


@pytest.mark.asyncio
async def test_a_dispatched_lane_scope_is_enforced_without_any_plan_bookkeeping():
    """The fan-out lane case: the host pinned the scope at dispatch, so the
    contract holds even if the worker never checked its task row off."""
    gi = _gate_input(_file_diff("src/core/db.py"), declared=("src/widget/**",))
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is False and "src/core/db.py" in (verdict.reason or "")


def test_scope_violation_blocks_under_trust_as_well_as_strict():
    assert "scope" in ALWAYS_HARD
    assert gate_consequence("scope", "trust") is Consequence.BLOCK
    assert gate_consequence("scope", "strict") is Consequence.BLOCK


@pytest.mark.asyncio
async def test_scope_gate_runs_after_integrity_and_before_review():
    """Ordering is the guarantee that a hermeticity violation costs zero tokens:
    the scope verdict short-circuits the chain ahead of every `claude` call."""
    review = _RecordingCognitionGate()
    gi = _gate_input(CLAIM_DIFF + _file_diff("infra/deploy.sh"))
    verdict = await run_pipeline(
        gi, (tq._VerifyGate(), tq._IntegrityGate(), tq._ScopeGate(), review)
    )
    assert verdict is not None and verdict.gate_id == "scope"
    assert review.calls == 0, "a scope violation must not spend cognition"


@pytest.mark.asyncio
async def test_a_plan_without_declared_scopes_leaves_the_gate_chain_byte_identical():
    """The hard requirement: an increment whose plan declares nothing sees the
    same verdict AND the same single diff computation as before this gate."""
    diff_calls: list = []
    review = _RecordingCognitionGate()
    gi = _gate_input(_file_diff("anywhere/at/all.py"), diff_calls=diff_calls)
    verdict = await run_pipeline(
        gi, (tq._VerifyGate(), tq._IntegrityGate(), tq._ScopeGate(), review)
    )
    assert verdict is None
    assert review.calls == 1  # the chain ran to completion, unchanged
    assert len(diff_calls) == 1  # still computed at most once, shared


@pytest.mark.asyncio
async def test_an_unreviewable_scope_check_fails_closed(monkeypatch):
    """A gate that runs and cannot produce a verdict never ships on its own
    silence (#186) — even though the parser is total so this should be
    unreachable."""

    def _boom(diff, declared=None):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(tq, "scope_check", _boom)
    verdict = await tq._ScopeGate().check(_gate_input(CLAIM_DIFF))
    assert verdict.ok is False
    assert "could not produce a verdict" in (verdict.reason or "")
    assert verdict.dialable is False


@pytest.mark.asyncio
async def test_a_spec_directory_claimed_at_runtime_fails_the_declared_scope_gate():
    """FR-104: spec-directory names are allocated by the task graph at planning
    time. A directory invented while the increment runs is by construction a
    path no declared scope names, so it fails — the requirement is enforced, not
    merely requested."""
    gi = _gate_input(
        CLAIM_DIFF
        + _file_diff("src/widget/a.py")
        + _file_diff("specs/014-invented-at-runtime/spec.md")
    )
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is False
    assert "specs/014-invented-at-runtime/spec.md" in (verdict.reason or "")


def test_sandbox_mcp_config_gives_the_worker_no_worker_spawn_surface():
    """FR-105: a worker must never spawn workers, and the enforcement is that it
    has nowhere to ask from — the baked sandbox MCP config carries no devclaw
    surface, so `create_goal` / `dispatch_task` / `start_program` are simply not
    reachable from inside a sandbox. The 2026-08-18 ruling rejected
    worker-spawned subagents outright; wiring devclaw's own MCP into the sandbox
    would quietly repeal it, so this fails the build instead."""
    cfg = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "runner" / "sandbox-mcp.json").read_text()
    )
    servers = cfg.get("mcpServers", {})
    assert servers, "the sandbox MCP config must still declare its servers explicitly"
    blob = json.dumps(cfg).lower()
    for forbidden in ("devclaw", "create_goal", "dispatch_task", "start_program"):
        assert forbidden not in blob, (
            f"{forbidden!r} reachable from the sandbox would let a worker spawn "
            f"workers (spec 010 FR-105)"
        )


# ---- completeness of the judged span (#630, spec 013) ----------------------


@pytest.mark.asyncio
async def test_an_unrecorded_out_of_scope_file_still_fails_the_gate(monkeypatch):
    """The #358 route-around through the back door: `git diff <base>` shows only
    what the agent CHOSE to record, while delivery stages everything in the
    workspace (#630). Without folding the unrecorded paths in, an increment
    escapes its declared scope by simply not committing the offending file. The
    gate consults the workspace, so it does not."""

    async def _unrecorded(_dir):
        return ["src/core/db.py", "notes.txt"]

    monkeypatch.setattr(tq, "_git_status_paths", _unrecorded)
    # the diff itself is spotless — everything recorded is inside the scope
    gi = _gate_input(_file_diff("src/widget/a.py"), declared=("src/widget/**",))
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is False
    assert "src/core/db.py" in (verdict.reason or "")
    assert "notes.txt" in (verdict.reason or "")


@pytest.mark.asyncio
async def test_the_completeness_probe_is_skipped_when_no_scope_was_declared(monkeypatch):
    """It costs one `git status`, and only once a contract exists: an increment
    that declared nothing pays nothing, so the ordinary path stays byte-identical
    down to its subprocess count."""
    calls: list = []

    async def _probe(d):
        calls.append(d)
        return []

    monkeypatch.setattr(tq, "_git_status_paths", _probe)
    verdict = await tq._ScopeGate().check(_gate_input(_file_diff("anything.py")))
    assert verdict.ok is True
    assert calls == []


@pytest.mark.asyncio
async def test_an_increment_that_records_nothing_at_all_claims_nothing(monkeypatch):
    """The residual half of #630, written down so nobody rediscovers it.

    A LANE is bound by the scope the host pinned, so unrecorded files are caught
    (above). An increment with no pinned scope is bound only by its own CLAIM on
    the task graph — and that claim is read from the recorded diff. If the agent
    records literally nothing, there is no claim, so no contract, so nothing to
    enforce. Spec 013 / #630 closes this by materialising the change
    mechanically before any gate reads it; when it lands, this test should
    become an assertion that the violation IS caught."""

    async def _unrecorded(_dir):
        return ["src/core/db.py"]

    monkeypatch.setattr(tq, "_git_status_paths", _unrecorded)
    verdict = await tq._ScopeGate().check(_gate_input(""))  # nothing recorded
    assert verdict.ok is True  # documents today's behaviour, not the desired one


def test_the_completeness_probe_reads_untracked_files_from_a_real_workspace(tmp_path):
    """The probe itself, against real git: an untracked file is part of what the
    increment changed, because it is part of what delivery will ship."""
    import subprocess

    from devclaw.task_git import _git_status_paths_sync

    d = str(tmp_path)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", "-C", d, *args], check=True, capture_output=True)
    (tmp_path / "kept.py").write_text("K = 1\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", d, "commit", "-q", "-m", "base"], check=True, capture_output=True
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "never_committed.py").write_text("N = 1\n")
    (tmp_path / "kept.py").write_text("K = 2\n")

    paths = set(_git_status_paths_sync(d))
    assert "sub/never_committed.py" in paths
    assert "kept.py" in paths
    # best-effort: a non-repo is no evidence, never a crash
    assert _git_status_paths_sync(str(tmp_path / "nope")) == []
