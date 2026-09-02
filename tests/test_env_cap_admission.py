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
from dataclasses import replace

import pytest

from devclaw import env_cap
from devclaw.config import goal_tick_seconds
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
async def test_hold_clears_without_an_operator_verb_when_the_probe_greens(tmp_path, monkeypatch):
    """US2/FR-004: the owner fixes the environment and the hold lifts on its
    own — no steer, no resume, no second ping.

    Driven through two REAL heartbeat sweeps, with the cached row aged between
    them, because the TTL is what makes "auto-resume within ~one sweep"
    reachable at all: a result that is still fresh when the next sweep reads it
    is never re-probed, so the tick reads the stale RED and the hold outlives
    the fix by a whole cadence. Writing the green row by hand would assert the
    heal while skipping the expiry that has to deliver it."""
    from devclaw import state_store as _state_store

    # env_cap resolves ``_now_ms`` through a deferred import, so patching it on
    # the package moves ONLY the probe cache's clock — every other writer bound
    # the symbol at import time. The narrowest seam for aging a cached row.
    clock_ms = [1_700_000_000_000]
    monkeypatch.setattr(_state_store, "_now_ms", lambda: clock_ms[0])

    verdict = ["red"]
    monkeypatch.setitem(env_cap._PROBE_RUNNERS, REGISTRY, lambda: CapProbeResult(
        status=verdict[0],
        evidence=f"probe says {verdict[0]}",
        remedy="rotate NODE_AUTH_TOKEN and redeploy" if verdict[0] == "red" else "",
    ))

    store = _seed(tmp_path, REGISTRY)
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    async def sweep():
        return await tick_all(
            store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
            notify_url="http://relay", prepare_ws=fake_prepare,
        )

    assert (await sweep())["g"] is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:env"
    assert REGISTRY in st.blocked_on and "rotate NODE_AUTH_TOKEN" in st.blocked_on
    assert engine.dispatched == [] and len(notifier.sent) == 1

    # The owner rotates the token; one heartbeat cadence passes. The TTL is
    # derived from that cadence precisely so the row is guaranteed stale here —
    # a TTL wider than a sweep would silently strand this goal for another one.
    verdict[0] = "green"
    assert env_cap.probe_ttl_s() < goal_tick_seconds()
    clock_ms[0] += goal_tick_seconds() * 1000

    assert (await sweep())["g"] is Outcome.DISPATCHED
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
        # Stand in for a NON-productive settle. Re-arm only the SCHEDULING
        # fields (budget + plan cadence) so the next tick reaches the
        # admission gate, and carry every damping counter forward via
        # replace(), as tick_settle does — rebuilding a bare GoalStatus here
        # would reset the episode markers the damping is MADE of and make the
        # ping assertion below vacuous.
        store.save_status("g", replace(
            store.load_status("g"), phase="idle", in_flight=None,
            actions_dispatched=0, last_plan_at=None,
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
async def test_an_unrelated_prior_heal_does_not_swallow_the_env_hold_ping(tmp_path):
    """FR-003/SC-002: the one owner ping is owed per ENVIRONMENT hold episode.

    ``heal_attempts`` is shared with every other ``mechanical:*`` auto-heal, so
    a goal that earlier healed a ``mechanical:prep`` block carries a non-zero
    count into an unrelated, genuine environment breakage. Gating the ping on
    that counter silently swallowed exactly the ping SC-002 promises — the
    brake would hold dispatch and tell nobody."""
    store = _seed(tmp_path, REGISTRY)
    store.save_status("g", GoalStatus(
        phase="idle", lifecycle="executing", heal_attempts=2,   # from a prep heal
    ))
    _probe(store, "red")
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    assert await _tick(store, engine, notifier, evaluator) is Outcome.BLOCKED
    assert len(notifier.sent) == 1 and REGISTRY in notifier.sent[0]
    assert store.load_status("g").env_hold_notified is True
    # ... and still exactly one: the episode marker, not the shared counter,
    # is what silences the re-holds.
    await _tick(store, engine, notifier, evaluator)
    assert len(notifier.sent) == 1
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_doctor_and_the_goal_block_name_the_same_probe_id(tmp_path, monkeypatch):
    """US3: an operator reading a ``mechanical:env`` hold and an operator
    reading doctor must see ONE story. Both surfaces name the capability id
    from the single constant in ``env_cap`` — a re-typed literal on either
    side is how the two drift into telling different stories about one fault."""
    from devclaw.doctor import checks_instance as ci

    cap = env_cap.CAP_REGISTRY_NPM_GITHUB
    # env_cap owns the credential rule; doctor re-exports it rather than
    # restating it, so the two can never disagree about one token.
    assert ci._probe_registry_token is env_cap.probe_registry_token
    assert ci._GH_TOKEN_PREFIXES is env_cap.GH_TOKEN_PREFIXES

    store = _seed(tmp_path, REGISTRY)
    _probe(store, "red")
    await _tick(store, FakeEngine(), RecordingNotifier(), FakeClaude())
    assert cap in store.load_status("g").blocked_on

    # The doctor side. ``check_registry_token`` reads os.environ, never ctx.
    monkeypatch.setattr(ci, "_probe_registry_token", lambda t, timeout_s=5.0: 401)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "ghp_wellformedbutrejected")
    (finding,) = ci.check_registry_token(None)  # type: ignore[arg-type]
    assert finding.verdict.value == "fail"
    assert cap in finding.remedy


@pytest.mark.asyncio
async def test_probes_run_once_per_sweep_and_never_on_the_per_goal_tick(tmp_path, monkeypatch):
    """FR-004: the network probe lives in the sweep pre-loop, not the tick. A
    per-goal tick reads persisted rows only (zero probe runs); ``tick_all`` runs
    each STALE declared probe once, and runs nothing for a capability that no
    LIVE goal's project declares — neither an undeclared one, nor one left
    behind by a terminal goal, whose workspace can never be dispatched into
    again and so must not buy the fleet a recurring network probe forever."""
    runs: list[str] = []

    def _fake(cap_id: str):
        def run() -> CapProbeResult:
            runs.append(cap_id)
            return CapProbeResult(status="green", evidence="fake")
        return run

    monkeypatch.setitem(env_cap._PROBE_RUNNERS, REGISTRY, _fake(REGISTRY))
    monkeypatch.setitem(env_cap._PROBE_RUNNERS, "sandbox:image", _fake("sandbox:image"))

    store = _seed(tmp_path, REGISTRY)
    # A cancelled goal on its own project, declaring the OTHER capability.
    cancelled_ws = _workspace(tmp_path / "gone", "sandbox:image")
    seed_goal(tmp_path / "goals", "dead", workspace_dir=cancelled_ws)
    store.save_status("dead", GoalStatus(phase="cancelled", lifecycle="executing"))
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    await _tick(store, engine, notifier, evaluator)
    assert runs == []                                       # the tick never probes

    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )
    # Declared by a live goal only, exactly once: the cancelled goal's
    # ``sandbox:image`` declaration buys no probe.
    assert runs == [REGISTRY]

    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )
    assert runs == [REGISTRY]                               # still TTL-fresh: no re-probe
