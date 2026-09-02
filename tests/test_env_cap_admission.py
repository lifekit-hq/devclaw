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
from types import SimpleNamespace

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


def _probe(
    store: GoalStore, status: str, cap_id: str = REGISTRY,
    project_id: "str | None" = None,
) -> None:
    # Evidence deliberately does NOT contain the capability id — the block must
    # name the probe id itself (US3: doctor and the block tell ONE story), and
    # the real registry probe's evidence is "NODE_AUTH_TOKEN rejected by …".
    env_cap._write_result(store, env_cap.CapTarget(cap_id, project_id), CapProbeResult(
        status=status,
        evidence=f"probe says {status}",
        remedy="rotate NODE_AUTH_TOKEN and redeploy" if status == "red" else "",
    ))


def _registered_caps(tmp_path, workspaces: "dict[str, str]") -> "dict[str, tuple[str, ...]]":
    """Run the REAL per-project resolver (``GoalService._registered_capabilities``)
    over a registry of ``project_id -> workspace_dir``."""
    from types import SimpleNamespace

    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    db = StateStore(str(tmp_path / "caps-state.db"))
    try:
        registry = SimpleNamespace(list=lambda: [
            SimpleNamespace(id=pid, workspace_dir=ws, status="active")
            for pid, ws in workspaces.items()
        ])
        svc = GoalService(
            TaskQueue(db), db,
            config=GoalConfig(
                goals_dir=tmp_path / "goals", notify_url="", tick_seconds=900,
                verify_done=False,
            ),
            project_registry=registry,  # type: ignore[arg-type]
        )
        return svc._registered_capabilities()
    finally:
        db.close()


async def _tick(store, engine, notifier, evaluator):
    return await tick_goal(
        "g", store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
    )


@pytest.mark.parametrize(
    "declared_via", ["goal_workspace", "project_registry", "project_registry_no_checkout"],
)
@pytest.mark.asyncio
async def test_red_capability_holds_dispatch_with_one_ping_and_zero_cognition(
    tmp_path, declared_via,
):
    """US1/SC-001: a red probe for a DECLARED capability holds the dispatch —
    no worker launched, a ``mechanical:env`` block naming the probe evidence and
    its remedy, exactly one owner ping, zero LLM calls. Holding it for further
    ticks stays free and silent (the pause_notified shape).

    Every declaration SOURCE holds identically. ``project_registry`` is the
    first-ever-dispatch case: the goal's workspace has NEVER been prepared, so
    reading the declaration out of it finds nothing and the goal would sail
    through into a session that cannot work. Only the sweep's registry-sourced
    map holds it, which is what SC-002's "zero worker sessions until the token
    is rotated" actually promises.

    ``project_registry_no_checkout`` runs the REAL resolver over a project
    whose checkout does not exist, and is the other half of that trade: the
    registry map is authoritative wherever it answers, so a project it could
    not read must be OMITTED from it rather than recorded as declaring
    nothing. Recording it turns "no answer" into a licence to dispatch and
    silently repeals the brake for every goal whose own workspace carries the
    declaration."""
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()
    if declared_via == "goal_workspace":
        store = _seed(tmp_path, REGISTRY)
        project_caps = None
    elif declared_via == "project_registry_no_checkout":
        # The goal BELONGS to the registered project (so an entry in the map
        # would answer for it) but the project's checkout does not exist; the
        # declaration lives in the goal's own prepared workspace.
        goals = tmp_path / "goals"
        store = GoalStore(goals, now=Clock())
        seed_goal(
            goals, "g", project_id="proj",
            workspace_dir=_workspace(tmp_path, REGISTRY),
        )
        store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
        project_caps = _registered_caps(tmp_path, {"proj": str(tmp_path / "never-cloned")})
        assert project_caps == {}             # omitted, NOT {"proj": ()}
    else:
        goals = tmp_path / "goals"
        store = GoalStore(goals, now=Clock())
        seed_goal(
            goals, "g", project_id="proj",
            workspace_dir=str(tmp_path / "never-prepared"),
        )
        store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
        project_caps = {"proj": (REGISTRY,)}
    _probe(store, "red")

    async def tick() -> Outcome:
        """Drive the registry case through the SWEEP, which is where the
        capability map is sourced; the workspace case needs no sweep."""
        if project_caps is None:
            return await _tick(store, engine, notifier, evaluator)
        outcomes = await tick_all(
            store=store, engine=engine, evaluator_caller=evaluator,
            notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
            project_capabilities=lambda: project_caps,
        )
        return outcomes["g"]

    assert await tick() is Outcome.BLOCKED

    st = store.load_status("g")
    assert st.phase == "blocked" and st.blocked_kind == "mechanical:env"
    assert REGISTRY in st.blocked_on                       # names the probe id
    assert "rotate NODE_AUTH_TOKEN" in st.blocked_on       # ... and the remedy
    assert engine.dispatched == []                         # zero workers burned
    assert len(notifier.sent) == 1
    assert REGISTRY in notifier.sent[0]
    assert evaluator.calls == 0

    # Held ticks stay free and silent — and, for the registry case, the hold
    # SURVIVES: the auto-heal must resolve the declaration the same way the
    # gate did, or an unprepared workspace reads "declares nothing" and clears
    # the block straight back into the red capability every tick.
    for _ in range(3):
        await tick()
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
    monkeypatch.setitem(env_cap._PROBE_RUNNERS, REGISTRY, lambda _t: CapProbeResult(
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
async def test_a_declared_capability_with_no_credential_at_all_is_red(tmp_path, monkeypatch):
    """FR-002/SC-002: the probe only runs because a project DECLARED the
    capability, so an ABSENT credential is the declared dependency missing —
    not the fail-open uncertainty of FR-007. Treating unset as a passing
    posture is the deterministic `npm ci` 401 burn class the brake exists to
    prevent: every dispatch would spend a session discovering, in the sandbox,
    something the host knew before launching it.

    Driven through the REAL probe runner (no network: an empty credential is
    decided before any request) so the verdict and its remedy are the ones an
    operator actually sees."""
    from devclaw.engine.sandcastle import REGISTRY_TOKEN_VAR

    monkeypatch.delenv(REGISTRY_TOKEN_VAR, raising=False)
    store = _seed(tmp_path, REGISTRY)
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    outcomes = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert outcomes["g"] is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:env" and REGISTRY in st.blocked_on
    assert f"set {REGISTRY_TOKEN_VAR}" in st.blocked_on      # the remedy is actionable
    assert engine.dispatched == [] and evaluator.calls == 0

    # Doctor tells the SAME story on the same state (US3): the instance check
    # keeps its unset-is-a-supported-posture verdict only while no project
    # declares the capability.
    from devclaw.doctor import checks_instance as ci

    ctx = SimpleNamespace(registry=SimpleNamespace(list=lambda: [
        SimpleNamespace(id="proj", workspace_dir=store.load_goal("g").workspace_dir,
                        status="active"),
    ]))
    (finding,) = ci.check_registry_token(ctx)  # type: ignore[arg-type]
    assert finding.verdict.value == "fail" and REGISTRY in finding.evidence
    (ok,) = ci.check_registry_token(
        SimpleNamespace(registry=SimpleNamespace(list=list)),  # type: ignore[arg-type]
    )
    assert ok.verdict.value == "ok"


def test_a_typoed_capability_id_fails_loud_instead_of_disabling_the_brake():
    """FR-005/FR-006: capability ids are value-validated at the manifest parse,
    the ``strictnessDefault``/``surface`` precedent — not tolerated like the
    informational ``stack``.

    An id no probe answers to is worse than none: the repo reads as protected
    while the brake is silently off, so ``registry:npmgithub`` would spend
    exactly the sessions the declaration was written to save. Loud at the
    doorway means prep and doctor reject it while a human is still watching."""
    from devclaw.project_manifest import ManifestError, parse_manifest

    for cap in list(env_cap.KNOWN_CAPABILITIES):
        assert parse_manifest(
            json.dumps({"schemaVersion": 1, "capabilities": [cap]}),
        ).capabilities == (cap,)
        # Same class, second instance: whitespace padding. Validation compares
        # the STRIPPED id, so the parse must also STORE it stripped — a padded
        # id kept raw keys nothing in `_PROBE_RUNNERS`, probes "unknown", and
        # FR-007 declines to hold on unknown. Identical silent-brake-off
        # outcome as the typo, reached without a typo.
        padded = parse_manifest(
            json.dumps({"schemaVersion": 1, "capabilities": [f"  {cap}\t\n"]}),
        ).capabilities
        assert padded == (cap,)
        assert all(c in env_cap._PROBE_RUNNERS for c in padded)

    with pytest.raises(ManifestError) as exc:
        parse_manifest(json.dumps({"schemaVersion": 1, "capabilities": ["registry:npmgithub"]}))
    assert "registry:npmgithub" in str(exc.value)
    assert REGISTRY in str(exc.value)                        # names what IS probeable


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
    assert st.env_heal_attempts >= ENV_HEAL_CAP
    assert st.heal_attempts == 0          # the prep budget was never touched

    _probe(store, "green")                                  # budget spent: no auto-heal
    assert await _tick(store, engine, notifier, evaluator) is not Outcome.DISPATCHED
    assert store.load_status("g").blocked_kind == "mechanical:env"
    assert len(notifier.sent) == 2                          # the gave-up ping, once
    assert "auto-recovery gave up" in notifier.sent[1]
    assert evaluator.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_heals", [2, ENV_HEAL_CAP + 1])
async def test_an_unrelated_prior_heal_does_not_swallow_the_env_brake(
    tmp_path, prior_heals,
):
    """FR-003/SC-002 + US2: the env brake owns BOTH its markers.

    ``heal_attempts`` is shared with every other ``mechanical:*`` auto-heal, so
    a goal that earlier healed ``mechanical:prep`` blocks carries a non-zero
    count into an unrelated, genuine environment breakage. Reading that shared
    counter cost the brake its two promises at once: the ping SC-002 owes was
    swallowed (the hold told nobody), and with a spent prep budget the goal was
    parked instead of auto-resuming when the probe greened — so the case is
    parametrized over a merely-nonzero count AND one past ``ENV_HEAL_CAP``."""
    store = _seed(tmp_path, REGISTRY)
    store.save_status("g", GoalStatus(
        phase="idle", lifecycle="executing", heal_attempts=prior_heals,
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

    # US2: the probe greens and the goal resumes on the very next tick, with
    # the prep budget still untouched.
    _probe(store, "green")
    assert await _tick(store, engine, notifier, evaluator) is Outcome.DISPATCHED
    st = store.load_status("g")
    assert st.blocked_kind == ""
    assert st.env_heal_attempts == 1
    assert st.heal_attempts == prior_heals
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
    each STALE declared probe once, from either source — a registered project's
    manifest or a live goal's workspace — and runs nothing for a capability no
    one declares, nor for one left behind by a terminal goal, whose workspace
    can never be dispatched into again and so must not buy the fleet a
    recurring network probe forever."""
    runs: list[str] = []
    targets: "list[env_cap.CapTarget]" = []

    def _fake(cap_id: str):
        def run(target: env_cap.CapTarget) -> CapProbeResult:
            runs.append(cap_id)
            targets.append(target)
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

    # A REGISTERED project declares the other capability. Its declaration buys
    # the probe even though no goal workspace on disk mentions it — that is the
    # source the admission gate reads, so the sweep must keep it fresh.
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        project_capabilities=lambda: {"proj": ("sandbox:image",)},
    )
    assert runs == [REGISTRY, "sandbox:image"]

    # SCOPE (CAP_SCOPES): ``sandbox:image`` is about the image THAT project's
    # sandbox launches, so a project pinning its own (ADR 0005) is probed —
    # and cached — apart from one on the fleet default. One fleet-wide row
    # answers about an image the project never runs, in BOTH directions: the
    # pinned project admitted because the default is pullable, or held because
    # it isn't. The instance-scoped registry credential is the opposite case:
    # one process-wide env var, so N projects buy exactly one probe.
    targets.clear()
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        project_capabilities=lambda: {
            "alpha": ("sandbox:image",), "beta": ("sandbox:image",),
        },
        project_images=lambda: {"beta": "devclaw-sandbox-dotnet:local"},
    )
    assert sorted((t.project_id, t.subject) for t in targets) == [
        ("alpha", None),                          # inherits the fleet default
        ("beta", "devclaw-sandbox-dotnet:local"),  # its own pin
    ]
    # ... and the results land in per-project rows, so one project's red never
    # holds the other.
    _probe(store, "red", "sandbox:image", project_id="beta")
    assert env_cap.red_caps_for(store, ("sandbox:image",), "beta")
    assert env_cap.red_caps_for(store, ("sandbox:image",), "alpha") == []
    # An instance-scoped capability ignores the project entirely — one row.
    assert env_cap.read_result(store, REGISTRY, "alpha") == env_cap.read_result(store, REGISTRY)
