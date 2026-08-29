"""Event-driven triggers (spec 023): the webhook route's auth posture and the
event router's wake/grade/drop table. The design invariant under test: an
event never grows a second transition path — it stamps a trigger-named goal
log line and pokes the same machinery the heartbeat drives (FR-002), grading
being the one direct action at exactly the manual verb's cognition (FR-007).
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from devclaw.goal import events as goal_events
from devclaw.goal.store import GoalStore
from devclaw.project_registry import ProjectRegistry
from devclaw.server.routes import webhooks as webhooks_route
from tests.goal_fakes import Clock, seed_goal

_REPO_URL = "https://github.com/lifekit-hq/demo.git"
_FULL = "lifekit-hq/demo"


def _registry(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="demo", name="demo", workspace_dir=str(tmp_path / "ws"),
               repo_url=_REPO_URL)
    return reg


def _stores(tmp_path):
    store = GoalStore(tmp_path / "goals", now=Clock())
    seed_goal(tmp_path / "goals", "g1", repo_url=_REPO_URL)
    return store


class _Recorder:
    def __init__(self):
        self.pokes = 0
        self.regrades: list[dict] = []

    def poke(self):
        self.pokes += 1

    async def regrade(self, registry, *, project_id, issue):
        self.regrades.append({"project_id": project_id, "issue": issue})
        return {"readiness": "devclaw-ready"}


def _payload(**kw):
    base = {"repository": {"full_name": _FULL}}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_pr_merged_event_wakes_goals_with_named_trigger(tmp_path):
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    out = await goal_events.route_event(
        "pull_request",
        _payload(action="closed", pull_request={"merged": True, "number": 12}),
        registry=reg, goal_store=store, poke=rec.poke, regrade=rec.regrade,
    )
    assert out["outcome"] == "woke"
    assert rec.pokes == 1
    assert rec.regrades == []          # zero cognition on the wake path (FR-007)
    log = store.recent_log("g1")
    assert "event: pull_request/closed" in log and "webhook" in log  # FR-008


@pytest.mark.asyncio
async def test_pr_closed_without_merge_is_ignored(tmp_path):
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    out = await goal_events.route_event(
        "pull_request", _payload(action="closed", pull_request={"merged": False}),
        registry=reg, goal_store=store, poke=rec.poke, regrade=rec.regrade,
    )
    assert out["outcome"] == "ignored" and rec.pokes == 0


@pytest.mark.asyncio
async def test_issue_closed_and_check_completed_wake_too(tmp_path):
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    for event, payload in (
        ("issues", _payload(action="closed", issue={"number": 3})),
        ("check_suite", _payload(action="completed")),
    ):
        out = await goal_events.route_event(
            event, payload, registry=reg, goal_store=store,
            poke=rec.poke, regrade=rec.regrade,
        )
        assert out["outcome"] == "woke"
    assert rec.pokes == 2 and rec.regrades == []


@pytest.mark.asyncio
async def test_issue_opened_and_edited_trigger_grading_at_manual_verb_cost(tmp_path):
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    for action in ("opened", "edited"):
        out = await goal_events.route_event(
            "issues",
            _payload(action=action,
                     issue={"number": 9, "html_url": "https://github.com/lifekit-hq/demo/issues/9"}),
            registry=reg, goal_store=store, poke=rec.poke, regrade=rec.regrade,
        )
        assert out["outcome"] == "graded"
    assert len(rec.regrades) == 2      # exactly one grade per event — FR-007
    assert rec.regrades[0] == {"project_id": "demo",
                               "issue": "https://github.com/lifekit-hq/demo/issues/9"}
    assert rec.pokes == 0


@pytest.mark.asyncio
async def test_unregistered_repo_is_dropped_with_reason_never_an_error(tmp_path):
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    out = await goal_events.route_event(
        "pull_request",
        {"action": "closed", "repository": {"full_name": "someone/else"},
         "pull_request": {"merged": True}},
        registry=reg, goal_store=store, poke=rec.poke, regrade=rec.regrade,
    )
    assert out["outcome"] == "dropped" and "not registered" in out["detail"]
    assert rec.pokes == 0


@pytest.mark.asyncio
async def test_grading_failure_is_loud_but_never_raises(tmp_path):
    reg, store = _registry(tmp_path), _stores(tmp_path)

    async def _boom(registry, *, project_id, issue):
        raise RuntimeError("cognition unavailable")

    out = await goal_events.route_event(
        "issues",
        _payload(action="opened", issue={"html_url": "https://x/issues/1"}),
        registry=reg, goal_store=store, poke=lambda: None, regrade=_boom,
    )
    assert out["outcome"] == "grade_failed"


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(tmp_path):
    # FR-003: replaying the same wake produces no second transition — the poke
    # is a no-op wake and every transition stays behind the tick's CAS. Here:
    # two deliveries, two log lines, zero state writes (phase untouched).
    reg, store, rec = _registry(tmp_path), _stores(tmp_path), _Recorder()
    payload = _payload(action="closed", pull_request={"merged": True, "number": 5})
    for _ in range(2):
        await goal_events.route_event(
            "pull_request", payload, registry=reg, goal_store=store,
            poke=rec.poke, regrade=rec.regrade,
        )
    assert store.load_status("g1").phase == "idle"   # no state written here, ever


# ---- the route's auth posture ----------------------------------------------


class _Req:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_route_is_off_without_a_secret(monkeypatch):
    monkeypatch.delenv("DEVCLAW_WEBHOOK_SECRET", raising=False)
    resp = await webhooks_route.github_webhook(_Req(b"{}", {}))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_route_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("DEVCLAW_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"zen": "x"}).encode()
    resp = await webhooks_route.github_webhook(
        _Req(body, {"X-Hub-Signature-256": "sha256=deadbeef", "X-GitHub-Event": "ping"}))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_route_answers_ping_and_accepts_events_fast(monkeypatch):
    monkeypatch.setenv("DEVCLAW_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"zen": "keep it logically awesome"}).encode()
    resp = await webhooks_route.github_webhook(
        _Req(body, {"X-Hub-Signature-256": _sign("s3cret", body),
                    "X-GitHub-Event": "ping"}))
    assert resp.status_code == 200

    routed: list = []

    async def _fake_route(event, payload, **kw):
        routed.append(event)
        return {"outcome": "ignored", "detail": ""}

    monkeypatch.setattr(webhooks_route._events, "route_event", _fake_route)
    body = json.dumps({"action": "closed",
                       "repository": {"full_name": _FULL}}).encode()
    resp = await webhooks_route.github_webhook(
        _Req(body, {"X-Hub-Signature-256": _sign("s3cret", body),
                    "X-GitHub-Event": "pull_request"}))
    assert resp.status_code == 202     # answered before the router ran
    # let the background task drain
    import asyncio

    await asyncio.sleep(0)
    for t in list(webhooks_route._background):
        await t
    assert routed == ["pull_request"]
