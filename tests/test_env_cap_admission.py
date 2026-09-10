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
- **loud failure over silent degradation (spec 038 / #818)**: the hold never
  claims a filing it did not perform — it names the issue it opened, or the
  rule or error that stopped it, and a filing outcome can never change the hold.
"""

from __future__ import annotations

import asyncio
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
from devclaw.queue import settle
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
    ((), "ci-red"),              # spec 032: ci:definition is implicit for REGISTERED projects only
])
async def test_only_a_red_probe_for_a_declared_capability_holds(tmp_path, declared, probe):
    """FR-005/FR-007: admission is fail-open everywhere except evidence of
    breakage in a capability the project itself declared."""
    store = _seed(tmp_path, *declared)
    if probe == "red-other":
        _probe(store, "red", cap_id="sandbox:image")       # declared: registry only
    elif probe == "ci-red":
        _probe(store, "red", cap_id=env_cap.CAP_CI_DEFINITION)  # an ad-hoc goal has no project CI
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

    # Doctor tells the SAME story on the same state (US3). Since tinyspec
    # durable-container-secrets (2026-09-04) the credential is required
    # instance-wide: doctor FAILs on absence under the production engine
    # whether or not any project declares the capability — "unset is a
    # supported posture unless declared" is how finance-sentry (declaring
    # nothing) burned a session while doctor said OK.
    from devclaw import config as _config
    from devclaw.doctor import checks_instance as ci

    monkeypatch.setattr(_config, "ENGINE", "")
    ctx = SimpleNamespace(registry=SimpleNamespace(list=lambda: [
        SimpleNamespace(id="proj", workspace_dir=store.load_goal("g").workspace_dir,
                        status="active"),
    ]))
    (finding,) = ci.check_registry_token(ctx)  # type: ignore[arg-type]
    assert finding.verdict.value == "fail" and REGISTRY in finding.evidence
    (undeclared,) = ci.check_registry_token(
        SimpleNamespace(registry=SimpleNamespace(list=list)),  # type: ignore[arg-type]
    )
    assert undeclared.verdict.value == "fail"  # required regardless of declarations


@pytest.mark.parametrize("block,ok", [
    ({"environment": {"image": "mcr.microsoft.com/dotnet/sdk:10.0", "services": ["postgres:14"], "tools": ["dotnet-ef@10"]}}, True),
    ({"environment": {"image": "", "services": []}}, False),
    ({"environment": {"tools": ["", "x"]}}, False),
    ({"environment": "postgres"}, False),
])
def test_a_declared_environment_parses_or_fails_loud(block, ok):
    """Spec 032 US4 (surface): the environment declaration is parsed like the
    validation block — absent is today's behaviour, malformed is loud, never a
    silently-ignored declaration that reads as protection."""
    import json as _json
    from devclaw.project_manifest import ManifestError, parse_manifest
    text = _json.dumps({"schemaVersion": 1, **block})
    if ok:
        m = parse_manifest(text)
        assert m.environment is not None and m.environment.services == ("postgres:14",)
        assert m.environment.tools == ("dotnet-ef@10",)
    else:
        with pytest.raises(ManifestError):
            parse_manifest(text)
    assert parse_manifest(_json.dumps({"schemaVersion": 1})).environment is None


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


_ENV_MARKER = settle.WORKER_ENV_MARKER


def _project_pair(tmp_path):
    """Two goals on one registered project (no capability declared): ``g``
    mid-flight, ``g2`` dispatch-ready."""
    from devclaw.goal.models import InFlight
    goals = tmp_path / "goals"
    store = GoalStore(goals, now=Clock())
    seed_goal(goals, "g", project_id="proj", workspace_dir=_workspace(tmp_path))
    seed_goal(goals, "g2", project_id="proj", workspace_dir=_workspace(tmp_path))
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add dotnet-ef migration"),
    ))
    store.save_status("g2", GoalStatus(phase="idle", lifecycle="executing"))
    return store


@pytest.mark.asyncio
async def test_a_worker_reported_deficiency_holds_every_goal_on_the_project_with_one_ping(tmp_path):
    """Spec 032 US2: a worker's ``BLOCKED: env — <item>`` is the pipeline's
    fact, not the owner's question. The settle holds the reporting goal on
    ``mechanical:env`` (no Problem, no re-dispatch), records ONE red row for the
    PROJECT, and every other goal on that project holds at admission — the
    pause-and-resume shape of a declared capability, at zero cognition."""
    from devclaw.goal.models import PollResult
    store = _project_pair(tmp_path)
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    failed = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail=f"{_ENV_MARKER} dotnet-ef not available in the sandbox — the sandbox lacks something the work needs",
    ))

    async def sweep(engine):
        return await tick_all(
            store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
            notify_url="http://relay", prepare_ws=fake_prepare,
            project_capabilities=lambda: {"proj": ()},
        )

    out = await sweep(failed)
    assert out["g"] is Outcome.BLOCKED
    sg = store.load_status("g")
    assert sg.blocked_kind == "mechanical:env" and sg.problem_id == ""
    assert "dotnet-ef" in (sg.blocked_on or "") and "worker:" in (sg.blocked_on or "")
    assert out["g2"] is Outcome.QUEUED          # g still held the lane when this sweep began
    # next sweep: g is blocked, so g2 is the runnable head — and the project's
    # worker-reported row holds it at admission before any worker launches
    out = await sweep(FakeEngine())
    assert out["g2"] is Outcome.BLOCKED
    assert store.load_status("g2").blocked_kind == "mechanical:env"
    assert failed.dispatched == []
    assert evaluator.calls == 0
    assert len(notifier.sent) == 2 and all("dotnet-ef" in m for m in notifier.sent)  # one ping per goal episode
    assert env_cap.worker_caps_for(store, "proj") == (env_cap.worker_cap_id("dotnet-ef not available in the sandbox"),)

    # held ticks stay free and silent
    for _ in range(3):
        await sweep(FakeEngine())
    assert store.load_status("g").blocked_kind == "mechanical:env"
    assert store.load_status("g2").blocked_kind == "mechanical:env"
    assert len(notifier.sent) == 2 and evaluator.calls == 0


@pytest.mark.asyncio
async def test_a_worker_reported_deficiency_survives_an_instance_env_ref_change(tmp_path, monkeypatch):
    """AMENDS spec 032 US2 / SC-004
    (specs/tiny/env-hold-observes-the-capability).

    SC-004 read a worker-reported row GREEN whenever ``instance_env_ref()``
    changed — "a new sandbox image or devclaw build IS the fix arriving". On a
    self-hosting instance that is false twice over: the sandbox image is tagged
    with the devclaw sha, so every unrelated merge moved the ref; and the gaps
    workers report are credentials, which ride env vars and move NO ref, so the
    real fix was invisible while every irrelevant one looked like a cure.
    fs-431 lost a genuine `actions:read` hold to an unrelated merge four hours
    later and was re-dispatched into the same sandbox.

    The hold must now SURVIVE a ref change. Only a human clears it.
    """
    from devclaw.goal.models import PollResult
    store = _project_pair(tmp_path)
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    failed = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail=f"{_ENV_MARKER} dotnet-ef not available in the sandbox — the sandbox lacks something the work needs",
    ))
    caps = {"proj": ()}

    async def sweep(engine):
        return await tick_all(
            store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
            notify_url="http://relay", prepare_ws=fake_prepare,
            project_capabilities=lambda: caps,
        )

    out = await sweep(failed)
    assert out["g"] is Outcome.BLOCKED
    assert (await sweep(FakeEngine()))["g2"] is Outcome.BLOCKED

    # devclaw ships an unrelated build: the environment identity changes, but
    # nothing about the reported gap did.
    monkeypatch.setattr(env_cap, "instance_env_ref", lambda: "new-image:abc|deadbeef")
    engine = FakeEngine()
    out = await sweep(engine)
    assert store.load_status("g").blocked_kind == "mechanical:env"
    assert store.load_status("g2").blocked_kind == "mechanical:env"
    assert engine.dispatched == [], "a redeploy must not release a worker-reported env hold"
    assert not any(
        "auto-resumed" in line
        for gid in ("g", "g2") for line in store.recent_log(gid).splitlines()
    )
    assert evaluator.calls == 0                      # held ticks stay free


@pytest.mark.asyncio
async def test_resume_goal_clears_the_worker_reported_gap_and_work_flows(tmp_path):
    """The ONE exit: a human vouches. ``resume_goal`` must reach the
    PROJECT-scoped worker row, not just the goal's fields — otherwise the goal
    unblocks, dispatches, hits the still-red row and re-blocks, which is what
    made the amended behaviour above safe to ship."""
    from devclaw.goal.models import PollResult
    store = _project_pair(tmp_path)
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    failed = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail=f"{_ENV_MARKER} dotnet-ef not available in the sandbox — the sandbox lacks something the work needs",
    ))
    caps = {"proj": ()}

    async def sweep(engine):
        return await tick_all(
            store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
            notify_url="http://relay", prepare_ws=fake_prepare,
            project_capabilities=lambda: caps,
        )

    assert (await sweep(failed))["g"] is Outcome.BLOCKED
    assert env_cap.worker_caps_for(store, "proj") != ()

    cleared = env_cap.clear_worker_deficiencies(store, "proj")
    assert len(cleared) == 1
    assert env_cap.worker_caps_for(store, "proj") == ()
    assert env_cap.red_caps_for(store, (), "proj") == []

    # idempotent — a second vouch is a no-op, never an error
    assert env_cap.clear_worker_deficiencies(store, "proj") == ()


_DEFICIENCY = "dotnet-ef not available in the sandbox"
#: Built from the queue's OWN constants, so an edit to the failure text that
#: breaks the goal layer's item split fails here instead of in production.
_DEFICIENCY_DETAIL = (
    f"{settle.WORKER_ENV_MARKER} {_DEFICIENCY}"
    f"{settle.WORKER_ENV_SUFFIX_HEAD} something the work needs. Owned by devclaw."
)


class _FakeGh:
    """The doorway's create-path adapter. Class-level counters so a test can
    assert the UNSET-self-repo path shells nothing at all."""

    constructed = 0
    creates: list = []
    comments: list = []
    next_number: "int | None" = 7
    hang_s: float = 0.0

    def __init__(self) -> None:
        type(self).constructed += 1

    async def ensure_label(self, repo: str, name: str) -> None:
        return None

    async def create_issue(self, repo: str, *, title: str, body: str, labels: list) -> "int | None":
        # `hang_s` stalls the create the way an unresponsive gh does, so the
        # caller's wall-clock bound cancels a real doorway call mid-flight.
        if type(self).hang_s:
            await asyncio.sleep(type(self).hang_s)
        type(self).creates.append((repo, title, tuple(labels)))
        return type(self).next_number

    async def comment_issue(self, repo: str, number: int, *, body: str) -> bool:
        type(self).comments.append((repo, number))
        return True

    async def reopen_issue(self, repo: str, number: int, *, comment: str) -> bool:
        return True


@pytest.fixture
def fake_gh(monkeypatch):
    from devclaw import issue_doorway
    _FakeGh.constructed, _FakeGh.creates, _FakeGh.comments = 0, [], []
    _FakeGh.next_number, _FakeGh.hang_s = 7, 0.0
    monkeypatch.setattr(issue_doorway, "GhCli", _FakeGh)
    return _FakeGh


def _deficiency_row(store) -> dict:
    """The deficiency's own catalog row — entering ``blocked`` records a second
    ``block`` row (kind = the blocked_kind), so select by kind, never by index."""
    rows = store._state.list_problems(category="block", include_issue=True)
    return next(r for r in rows if r["kind"] == "env_deficiency")


def _seed_catalog_row(store, item: str = _DEFICIENCY) -> None:
    """What ``queue/settle.py`` records before the goal layer settles the
    deficiency — the row the filing links itself to."""
    store.record_problem(
        category="block", kind="env_deficiency", message=item,
        recovered=False, goal_id="g", task_id="t1",
    )


@pytest.mark.asyncio
async def test_a_worker_reported_deficiency_is_filed_and_the_hold_names_the_issue(
    tmp_path, monkeypatch, fake_gh,
):
    """Spec 038 / #818: the pipeline promised "filed as devclaw work" and filed
    nothing — filing lived on the once-per-cycle edge behind a two-run-cycle
    recurrence bar, and the hold this settle places is exactly what stops the
    failure from recurring. So the gap is filed HERE, through the one machine
    doorway, and the log + block text carry the issue it opened. A repeat
    report never opens a second issue (one fingerprint, one issue, ever)."""
    from devclaw.goal import env_issue
    from devclaw.goal.models import PollResult
    monkeypatch.setenv("DEVCLAW_SELF_REPO", "lifekit-hq/devclaw")
    store = _project_pair(tmp_path)
    _seed_catalog_row(store)
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    failed = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail=_DEFICIENCY_DETAIL,
    ))

    out = await tick_all(
        store=store, engine=failed, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        project_capabilities=lambda: {"proj": ()},
    )

    assert out["g"] is Outcome.BLOCKED
    sg = store.load_status("g")
    assert sg.blocked_kind == "mechanical:env"           # the hold is unchanged
    expected = "filed as devclaw work: #7 (https://github.com/lifekit-hq/devclaw/issues/7)"
    assert expected in (sg.blocked_on or "")
    assert expected in store.recent_log("g")
    assert any(expected in m for m in notifier.sent)
    assert len(fake_gh.creates) == 1
    repo, title, labels = fake_gh.creates[0]
    assert repo == "lifekit-hq/devclaw" and _DEFICIENCY in title
    assert "devclaw:self-filed" in labels                # stage-2 pickup still reaches it
    # the catalog row is linked, so the cycle-close filer reads it as tracked
    row = _deficiency_row(store)
    assert (row["issue_number"], row["issue_state"]) == (7, "open")
    assert evaluator.calls == 0

    # a repeat of the SAME gap comments on #7 instead of opening a second issue
    again = await env_issue.file_env_deficiency(
        store, goal_id="g2", project_id="proj", item=_DEFICIENCY,
        cap_id=env_cap.worker_cap_id(_DEFICIENCY), task_id="t2",
    )
    assert again.issue_number == 7 and again.filed is False
    assert "already tracked as devclaw work: #7" in again.line
    assert len(fake_gh.creates) == 1 and len(fake_gh.comments) == 1


def test_a_filed_environment_gap_is_never_auto_closed_as_stale():
    """The same deadlock as #818, one layer over: the age-out exit closes an
    open self-filed issue that has gone quiet, and an environment gap goes
    quiet BY CONSTRUCTION — the hold is what stops it recurring. Left in, the
    hold's "filed as devclaw work: #N" would point at an issue devclaw closed
    itself while the project was still red."""
    from devclaw.goal import self_issue
    quiet = {"issue_state": "open", "issue_number": 7, "last_seen_ms": 0}
    assert self_issue.should_close_stale({**quiet, "kind": "engine_error"}, now_ms=10**12)
    assert not self_issue.should_close_stale({**quiet, "kind": "env_deficiency"}, now_ms=10**12)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "self_repo, gh_number, fault, expect",
    [
        ("", 7, "", "NOT filed as devclaw work: DEVCLAW_SELF_REPO is unset"),
        ("lifekit-hq/devclaw", None, "",
         "NOT filed as devclaw work: issue creation failed (gh)"),
        ("lifekit-hq/devclaw", 7, "hang",
         "NOT filed as devclaw work: gh did not answer within"),
        ("lifekit-hq/devclaw", 7, "raise",
         "NOT filed as devclaw work: RuntimeError: ledger unavailable"),
    ],
    ids=["self-repo-unconfigured", "doorway-failed", "gh-hung-past-the-bound",
         "doorway-raised"],
)
async def test_a_deficiency_that_cannot_be_filed_says_why_and_still_holds(
    tmp_path, monkeypatch, fake_gh, self_repo, gh_number, fault, expect,
):
    """The other half of the same invariant: when there is no issue, the text
    says which rule or error stopped it — never a claim with no record behind
    it. The hold, its one ping and its heal are untouched either way, and an
    unconfigured self-repo shells nothing.

    The last two cases are the ones the doorway CANNOT record for itself: its
    own ``_fail`` never runs when the wall-clock bound cancels it mid-call or
    when something raises before it is entered. A stated clause with no catalog
    row behind it is #818's silence one level in — so the caller records it."""
    from devclaw import issue_doorway
    from devclaw.goal import env_issue
    from devclaw.goal.models import PollResult
    monkeypatch.setenv("DEVCLAW_SELF_REPO", self_repo)
    fake_gh.next_number = gh_number
    if fault == "hang":
        fake_gh.hang_s = 0.5
        monkeypatch.setattr(env_issue, "FILING_TIMEOUT_S", 0.05)
    elif fault == "raise":
        async def _boom(*a, **kw):
            raise RuntimeError("ledger unavailable")
        monkeypatch.setattr(issue_doorway, "file_finding", _boom)
    store = _project_pair(tmp_path)
    _seed_catalog_row(store)
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    failed = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail=_DEFICIENCY_DETAIL,
    ))

    out = await tick_all(
        store=store, engine=failed, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        project_capabilities=lambda: {"proj": ()},
    )

    assert out["g"] is Outcome.BLOCKED
    sg = store.load_status("g")
    assert sg.blocked_kind == "mechanical:env" and sg.problem_id == ""
    assert _DEFICIENCY in (sg.blocked_on or "") and expect in (sg.blocked_on or "")
    assert expect in store.recent_log("g")
    assert any(expect in m for m in notifier.sent)
    assert env_cap.worker_caps_for(store, "proj") == (env_cap.worker_cap_id(_DEFICIENCY),)
    if not self_repo:
        assert fake_gh.constructed == 0                  # no repo ⇒ nothing shelled
    else:
        # a failed filing is loud on the catalog too, never silence — and ONE
        # attempt is ONE occurrence, so a second recorder on the same exit
        # (the doorway's and the caller's both firing) fails here.
        failed = [r for r in store._state.list_problems(category="delivery")
                  if r["kind"] == "issue_filing_failed"]
        assert len(failed) == 1 and failed[0]["count"] == 1
    assert _deficiency_row(store)["issue_number"] is None
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_a_registered_project_without_a_ci_definition_is_held(tmp_path):
    """Spec 032 Q3: a registered project's own CI is its verification
    environment, so ``ci:definition`` is implicit for every registered project
    (one place: the registry map) and red holds dispatch like any declared
    capability — onboarding writes the workflow, nobody is asked."""
    store = _seed(tmp_path)                      # declares nothing in its manifest
    project_caps = _registered_caps(tmp_path, {"proj": _workspace(tmp_path)})
    assert project_caps == {"proj": (env_cap.CAP_CI_DEFINITION,)}
    goals = tmp_path / "goals"
    seed_goal(goals, "g", project_id="proj", workspace_dir=_workspace(tmp_path))
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    _probe(store, "red", cap_id=env_cap.CAP_CI_DEFINITION, project_id="proj")
    engine, notifier, evaluator = FakeEngine(), RecordingNotifier(), FakeClaude()

    out = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        project_capabilities=lambda: project_caps,
    )
    assert out["g"] is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:env" and env_cap.CAP_CI_DEFINITION in st.blocked_on
    assert engine.dispatched == [] and evaluator.calls == 0


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


# ---- spec 042 US2: a mechanical fact outranks a worker's sentence ----------
# A missing credential is a broken mount, and a broken mount is devclaw's own.
# Today it costs a whole agent session to discover, arrives as prose, becomes a
# project-wide brake keyed on that prose, and needs a human to clear. Each of
# those steps is pinned below.

@pytest.mark.parametrize("present, prose, expect_row", [
    # the runner saw it arrive → the report is FALSE → no row at all
    (["NODE_AUTH_TOKEN"], "no NODE_AUTH_TOKEN, so npm ci 401s", False),
    # nothing reported yet → absence of evidence is not evidence → today's row
    ([], "no NODE_AUTH_TOKEN, so npm ci 401s", True),
    # a different credential arrived; this one still missing → row stands
    (["CLAUDE_CODE_OAUTH_TOKEN"], "no NODE_AUTH_TOKEN, so npm ci 401s", True),
    # names no registered credential → the worker is the only witness → row
    (["NODE_AUTH_TOKEN"], "dotnet-ef not available in the sandbox", True),
])
def test_a_credential_that_reached_the_agent_brakes_nothing(tmp_path, present, prose, expect_row):
    store = _project_pair(tmp_path)
    env_cap.record_agent_env(store, present, [], task_id="t1", goal_id="g")
    cap_id = env_cap.record_worker_deficiency(store, "proj", prose, goal_id="g", task_id="t1")
    assert bool(cap_id) is expect_row
    assert bool(env_cap.worker_caps_for(store, "proj")) is expect_row


@pytest.mark.parametrize("wording", [
    "no NODE_AUTH_TOKEN for GitHub Packages, so verifyCmd's npm ci 401s",
    "NODE_AUTH_TOKEN (GitHub Packages `read:packages`) is absent, so npm ci 401s",
    "NODE_AUTH_TOKEN (GitHub token with `read:packages`) for npm.pkg.github.com",
])
def test_one_missing_credential_is_one_row_whatever_the_worker_calls_it(tmp_path, wording):
    """These three wordings are verbatim from the live catalog on 2026-09-10.
    They made three rows for one variable, each needing its own clearing."""
    store = _project_pair(tmp_path)
    cap_id = env_cap.record_worker_deficiency(store, "proj", wording)
    assert cap_id == env_cap.credential_cap_id("NODE_AUTH_TOKEN")


def test_a_credential_gap_heals_when_the_next_session_reports_it_present(tmp_path):
    """The release condition `mechanical:` promises. Fix the mount, redeploy —
    the next session clears the hold. No resume_goal, no human vouch."""
    store = _project_pair(tmp_path)
    cap_id = env_cap.record_worker_deficiency(store, "proj", "no NODE_AUTH_TOKEN in the sandbox")
    assert (env_cap.read_result(store, cap_id, "proj") or _R()).status == "red"

    env_cap.record_agent_env(store, ["NODE_AUTH_TOKEN"], [], task_id="t2")
    healed = env_cap.read_result(store, cap_id, "proj")
    assert healed is not None and healed.status == "green"
    assert "reached the agent environment" in healed.evidence


def test_a_credential_gap_is_devclaws_own_hop_not_the_owners_errand(tmp_path):
    store = _project_pair(tmp_path)
    cap_id = env_cap.record_worker_deficiency(store, "proj", "no NODE_AUTH_TOKEN in the sandbox")
    row = env_cap.read_result(store, cap_id, "proj")
    assert row is not None
    assert "the hop is broken" in row.evidence
    assert "redeploy" in row.remedy and "no resume_goal needed" in row.remedy


def test_a_non_credential_gap_keeps_the_human_exit(tmp_path):
    """A missing tool, no Docker daemon, the wrong arch: the worker is the only
    witness and devclaw can probe nothing, so this row is unchanged."""
    store = _project_pair(tmp_path)
    cap_id = env_cap.record_worker_deficiency(store, "proj", _DEFICIENCY)
    row = env_cap.read_result(store, cap_id, "proj")
    assert cap_id == env_cap.worker_cap_id(_DEFICIENCY)
    assert row is not None and row.status == "red" and row.remedy.startswith("provide ")

    env_cap.record_agent_env(store, ["NODE_AUTH_TOKEN"], [], task_id="t2")
    assert (env_cap.read_result(store, cap_id, "proj") or _R()).status == "red"


def test_the_session_report_never_carries_a_credential_value(tmp_path):
    """Names only. The report crosses from the sandbox into a persisted row, so
    a value here would print a live credential into meta and into doctor."""
    store = _project_pair(tmp_path)
    env_cap.record_agent_env(store, ["NODE_AUTH_TOKEN"], ["CLAUDE_CODE_OAUTH_TOKEN"])
    last = env_cap.agent_env_last(store) or {}
    assert last["present"] == ["NODE_AUTH_TOKEN"] and last["absent"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert set(last) == {"present", "absent", "task_id", "goal_id", "env_ref", "at_ms"}


def _R():
    return env_cap.CapProbeResult("unknown")
