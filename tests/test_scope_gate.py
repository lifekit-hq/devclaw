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
    out-of-scope file the agent never committed is still a violation (#630),
    and the gate gets that for free from the materialized span (spec 013)
    rather than probing for it itself.

Unit tests — no docker, no claude; the completeness tests shell out to real git
because materialization is a real git operation.
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


def _real_repo(tmp_path):
    """A real git workspace whose worker recorded SOME of its work."""
    import subprocess

    d = tmp_path / "ws"
    (d / "specs" / "010-feat").mkdir(parents=True)
    (d / "specs" / "010-feat" / "tasks.md").write_text(
        "- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
    )
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "base"],
                   check=True, capture_output=True)
    base = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return d, base


def _materialized_input(workspace, base, *, declared=()) -> GateInput:
    return GateInput(
        kind="implement_feature",
        goal="g",
        workspace_dir=str(workspace),
        verify={"ran": True, "passed": True},
        scaffold=False,
        browser_mode="flexible",
        change_fn=lambda: tq._capture_change(
            str(workspace), base, task_id="t1", message="chore: capture",
        ),
        declared_scope=declared,
    )


@pytest.mark.asyncio
async def test_an_unrecorded_out_of_scope_file_still_fails_the_gate(tmp_path):
    """The #358 route-around through the back door: `git diff <base>` used to
    show only what the agent CHOSE to record, while delivery staged everything
    in the workspace (#630). An increment escaped its declared scope by simply
    not committing the offending file. The span the gate reads is materialized
    now (spec 013), so the file is in it — and the gate needs no probe of its
    own to see it."""
    ws, base = _real_repo(tmp_path)
    (ws / "src" / "widget").mkdir(parents=True)
    (ws / "src" / "widget" / "a.py").write_text("W = 1\n")
    (ws / "src" / "core").mkdir(parents=True)
    (ws / "src" / "core" / "db.py").write_text("D = 1\n")  # never recorded
    (ws / "notes.txt").write_text("scratch\n")             # never recorded

    verdict = await tq._ScopeGate().check(
        _materialized_input(ws, base, declared=("src/widget/**",))
    )
    assert verdict.ok is False
    assert "src/core/db.py" in (verdict.reason or "")
    assert "notes.txt" in (verdict.reason or "")


@pytest.mark.asyncio
async def test_the_scope_gate_makes_no_probe_of_its_own(monkeypatch):
    """SC-004: exactly ONE place answers "what did the agent change?". The gate
    briefly carried its own `git status --untracked-files=all` to patch the
    completeness gap; the gap is closed upstream now, so a second computation
    here would be a third component owning the definition of the change."""
    calls: list = []

    async def _forbidden(*a, **kw):  # pragma: no cover — must never run
        calls.append(a)
        return ""

    monkeypatch.setattr(tq, "_git_diff", _forbidden)
    gi = _gate_input(CLAIM_DIFF + _file_diff("src/widget/a.py"))
    verdict = await tq._ScopeGate().check(gi)
    assert verdict.ok is True
    assert calls == []


@pytest.mark.asyncio
async def test_an_increment_that_records_nothing_at_all_is_still_judged_in_full(tmp_path):
    """The residual half of #630, now closed. An increment with no PINNED scope
    is bound by its own claim on the task graph — and that claim used to be read
    from the recorded diff, so an agent that recorded literally nothing made no
    claim, had no contract, and was enforced against nothing. Materialization
    captures the whole workspace before any gate reads it, so the claim and the
    violation both surface even though the agent committed neither."""
    ws, base = _real_repo(tmp_path)
    # the worker checks its own row off and writes out of scope — recording NOTHING
    (ws / "specs" / "010-feat" / "tasks.md").write_text(
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
    )
    (ws / "src" / "core").mkdir(parents=True)
    (ws / "src" / "core" / "db.py").write_text("D = 1\n")

    verdict = await tq._ScopeGate().check(_materialized_input(ws, base))
    assert verdict.ok is False
    assert "src/core/db.py" in (verdict.reason or "")
