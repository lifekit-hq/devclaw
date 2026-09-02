"""Environment-capability admission — the brake spec 030 adds (issue #792).

Tripwire classes pinned here (rules/testing.md):
- **pause/brake machinery**: a provably-broken capability holds dispatch with a
  ``mechanical:env`` block, pings the owner ONCE per hold episode, auto-resumes
  with no operator verb when the probe greens, and parks a flapping capability
  instead of cycling forever.
- **zero-token idle**: a held goal costs zero cognition per tick, and the
  per-goal tick path never probes a network — probes run once per heartbeat
  sweep in ``tick_all`` and only for capabilities a project actually declares.
- **fail-open on uncertainty (FR-007)**: absent/unknown/green results, and a
  project that declares nothing, dispatch exactly as they do today (SC-003).
"""

from __future__ import annotations

import json

import pytest

from devclaw import env_cap
from devclaw.env_cap import CapProbeResult
from devclaw.goal.models import GoalStatus
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_all, tick_goal
from devclaw.goal.tick_guards import ENV_HEAL_CAP
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)

REGISTRY = "registry:npm-github"


def _workspace(tmp_path, *capabilities: str) -> str:
    """A project workspace whose devclaw.json declares ``capabilities``."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    manifest = {"schemaVersion": 1, "boilerplateRevision": 1}
    if capabilities:
        manifest["capabilities"] = list(capabilities)
    (ws / "devclaw.json").write_text(json.dumps(manifest))
    return str(ws)


def _seed(tmp_path, *capabilities: str) -> GoalStore:
    """A dispatch-ready goal on a project declaring ``capabilities``."""
    goals = tmp_path / "goals"
    store = GoalStore(goals, now=Clock())
    seed_goal(goals, "g", workspace_dir=_workspace(tmp_path, *capabilities))
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    return store


def _probe(store: GoalStore, status: str, cap_id: str = REGISTRY) -> None:
    # Evidence deliberately does NOT contain the capability id — the block must
    # name the probe id itself (US3: doctor and the block tell ONE story), and
    # the real registry probe's evidence is "NODE_AUTH_TOKEN rejected by …".
    env_cap._write_result(store, cap_id, CapProbeResult(
        status=status,
        evidence=f"probe says {status}",
        remedy="rotate NODE_AUTH_TOKEN and redeploy" if status == "red" else "",
    ))


async def _tick(store, engine, notifier, evaluator):
    return await tick_goal(
        "g", store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
    )


@pytest.mark.asyncio
async def test_red_capability_holds_dispatch_with_one_ping_and_zero_cognition(tmp_path):
    """US1/SC-001: a red probe for a DECLARED capability holds the dispatch —
    no worker launched, a ``mechanical:env`` block naming the probe evidence and
    its remedy, exactly one owner ping, zero LLM calls. Holding it for further
    ticks stays free and silent (the pause_notified shape)."""
    store = _seed(tmp_path, REGISTRY)
    _probe(store, "red")
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    assert await _tick(store, engine, notifier, evaluator) is Outcome.BLOCKED

    st = store.load_status("g")
    assert st.phase == "blocked" and st.blocked_kind == "mechanical:env"
    assert REGISTRY in st.blocked_on                       # names the probe id
    assert "rotate NODE_AUTH_TOKEN" in st.blocked_on       # ... and the remedy
    assert engine.dispatched == []                         # zero workers burned
    assert len(notifier.sent) == 1
    assert REGISTRY in notifier.sent[0]
    assert evaluator.calls == 0

    for _ in range(3):
        await _tick(store, engine, notifier, evaluator)
    assert store.load_status("g").blocked_kind == "mechanical:env"
    assert engine.dispatched == [] and len(notifier.sent) == 1
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_hold_clears_without_an_operator_verb_when_the_probe_greens(tmp_path):
    """US2: the owner fixes the environment; the next sweep's probe result is
    green and the held goal resumes on its own tick — no steer, no resume, and
    no second ping."""
    store = _seed(tmp_path, REGISTRY)
    _probe(store, "red")
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()
    assert await _tick(store, engine, notifier, evaluator) is Outcome.BLOCKED

    _probe(store, "green")
    assert await _tick(store, engine, notifier, evaluator) is Outcome.DISPATCHED

    st = store.load_status("g")
    assert st.blocked_kind == "" and st.phase == "in_flight"
    assert len(engine.dispatched) == 1
    assert len(notifier.sent) == 1                         # the heal logs, never pings
    assert "auto-resumed: required capabilities are green" in store.recent_log("g")
    assert evaluator.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,probe", [
    ((REGISTRY,), "green"),      # capability healthy
    ((REGISTRY,), "unknown"),    # FR-007: an unrunnable probe is not evidence
    ((REGISTRY,), None),         # never probed → treated as unknown
    ((), "red"),                 # SC-003: declares nothing ⇒ held by nothing
    ((REGISTRY,), "red-other"),  # a red probe for an UNdeclared capability
])
async def test_only_a_red_probe_for_a_declared_capability_holds(tmp_path, declared, probe):
    """FR-005/FR-007: admission is fail-open everywhere except evidence of
    breakage in a capability the project itself declared."""
    store = _seed(tmp_path, *declared)
    if probe == "red-other":
        _probe(store, "red", cap_id="sandbox:image")       # declared: registry only
    elif probe is not None:
        _probe(store, probe)
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    assert await _tick(store, engine, notifier, evaluator) is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1
    assert store.load_status("g").blocked_kind == ""


@pytest.mark.asyncio
async def test_a_flapping_capability_converges_to_held_with_one_ping(tmp_path):
    """Spec 030 edge case: a probe oscillating green↔red must not ping per
    cycle. The first hold pings; every re-hold inside the same episode is
    log-only, and the heal budget parks the goal for the owner rather than
    cycling hold→resume→hold forever."""
    store = _seed(tmp_path, REGISTRY)
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    _probe(store, "red")
    assert await _tick(store, engine, notifier, evaluator) is Outcome.BLOCKED
    assert len(notifier.sent) == 1                         # the one hold ping

    for _ in range(ENV_HEAL_CAP):
        _probe(store, "green")
        await _tick(store, engine, notifier, evaluator)    # heals, dispatches
        store.save_status("g", GoalStatus(
            phase="idle", lifecycle="executing",
            heal_attempts=store.load_status("g").heal_attempts,
        ))
        _probe(store, "red")
        await _tick(store, engine, notifier, evaluator)    # re-holds, silently

    assert len(notifier.sent) == 1                         # no ping storm
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:env"
    assert st.heal_attempts >= ENV_HEAL_CAP

    _probe(store, "green")                                  # budget spent: no auto-heal
    assert await _tick(store, engine, notifier, evaluator) is not Outcome.DISPATCHED
    assert store.load_status("g").blocked_kind == "mechanical:env"
    assert len(notifier.sent) == 2                          # the gave-up ping, once
    assert "auto-recovery gave up" in notifier.sent[1]
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_probes_run_once_per_sweep_and_never_on_the_per_goal_tick(tmp_path, monkeypatch):
    """FR-004: the network probe lives in the sweep pre-loop, not the tick. A
    per-goal tick reads persisted rows only (zero probe runs); ``tick_all`` runs
    each STALE declared probe once, and runs nothing for a capability no
    registered project declares."""
    runs: list[str] = []

    def _fake(cap_id: str):
        def run() -> CapProbeResult:
            runs.append(cap_id)
            return CapProbeResult(status="green", evidence="fake")
        return run

    monkeypatch.setitem(env_cap._PROBE_RUNNERS, REGISTRY, _fake(REGISTRY))
    monkeypatch.setitem(env_cap._PROBE_RUNNERS, "sandbox:image", _fake("sandbox:image"))

    store = _seed(tmp_path, REGISTRY)
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    await _tick(store, engine, notifier, evaluator)
    assert runs == []                                       # the tick never probes

    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )
    assert runs == [REGISTRY]                               # declared only, exactly once

    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )
    assert runs == [REGISTRY]                               # still TTL-fresh: no re-probe
