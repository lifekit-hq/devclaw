"""Regression tests for the console-facing Resume goal route
(``POST /goals/{id}/resume`` in devclaw/server/http.py).

This gives the operator a control the console previously lacked — resume a
blocked goal whose blocker cleared out-of-band — so the daily "it pinged me,
I clear it" loop no longer needs an MCP call. The route is a thin wrapper over
goal_service.resume_goal; these pin the wiring: the service is actually called
with the right args, and each failure mode maps to the right HTTP status (404
unknown goal), never a silent 200. (The /answer route died with the firming
phase, spec 008 shrink.)
"""

from __future__ import annotations

import asyncio
import json

from starlette.requests import Request


def _req(path_params, body=None):
    scope = {
        "type": "http",
        "method": "POST",
        "path_params": path_params,
        "headers": [],
    }

    async def receive():
        raw = json.dumps(body).encode() if body is not None else b""
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


class _FakeGoals:
    """Minimal GoalService stand-in — records calls, raises injected errors."""

    def __init__(self, resume=None, strictness=None, verify_cmd=None):
        self._resume = resume
        self._strictness = strictness
        self._verify_cmd = verify_cmd
        self.resume_calls: list = []
        self.strictness_calls: list = []
        self.verify_cmd_calls: list = []

    def resume_goal(self, goal_id):
        self.resume_calls.append(goal_id)
        if isinstance(self._resume, Exception):
            raise self._resume
        return self._resume

    def set_strictness(self, goal_id, strictness):
        self.strictness_calls.append((goal_id, strictness))
        if isinstance(self._strictness, Exception):
            raise self._strictness
        return self._strictness

    def set_verify_cmd(self, goal_id, verify_cmd):
        self.verify_cmd_calls.append((goal_id, verify_cmd))
        if isinstance(self._verify_cmd, Exception):
            raise self._verify_cmd
        return self._verify_cmd


def _call(fn, req):
    resp = asyncio.run(fn(req))
    return resp.status_code, json.loads(resp.body)


# ── resume ─────────────────────────────────────────────────────────────────

def test_resume_calls_service_and_returns_result(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    fake = _FakeGoals(resume={"goal_id": "g", "resumed": True})
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(http_mod.goal_resume, _req({"goal_id": "g"}))
    assert status == 200 and body["resumed"] is True
    assert fake.resume_calls == ["g"]


def test_resume_unknown_goal_is_404(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    monkeypatch.setattr(http_mod, "goals", _FakeGoals(resume=KeyError("g")))
    status, body = _call(http_mod.goal_resume, _req({"goal_id": "g"}))
    assert status == 404 and body["error"] == "not_found"


# ── strictness (ADR 0007) ────────────────────────────────────────────────────

def test_strictness_forwards_valid_value_to_service(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    fake = _FakeGoals(strictness={"goal_id": "g", "strictness": "strict"})
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(http_mod.goal_strictness, _req({"goal_id": "g"}, {"strictness": "strict"}))
    assert status == 200 and body["strictness"] == "strict"
    assert fake.strictness_calls == [("g", "strict")]


def test_strictness_bad_value_is_400_without_calling_service(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    fake = _FakeGoals(strictness={"goal_id": "g", "strictness": "trust"})
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(http_mod.goal_strictness, _req({"goal_id": "g"}, {"strictness": "urgent"}))
    assert status == 400 and body["error"] == "strictness_required"
    assert fake.strictness_calls == []  # rejected before the service is touched


def test_strictness_unknown_goal_is_404(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    monkeypatch.setattr(http_mod, "goals", _FakeGoals(strictness=KeyError("g")))
    status, body = _call(http_mod.goal_strictness, _req({"goal_id": "g"}, {"strictness": "strict"}))
    assert status == 404 and body["error"] == "not_found"


# ── verify_cmd (issue #711) ─────────────────────────────────────────────────

def test_verify_cmd_update_forwarded_to_service(monkeypatch):
    # Named regression test: the HTTP route wires straight to the service and
    # reflects the returned dict — the operator can override verify_cmd without
    # cancelling + recreating the goal.
    from devclaw.server.routes import goals as http_mod
    ret = {"goal_id": "g", "verify_cmd": "pytest -x --timeout=60"}
    fake = _FakeGoals(verify_cmd=ret)
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(
        http_mod.goal_verify_cmd,
        _req({"goal_id": "g"}, {"verify_cmd": "pytest -x --timeout=60"}),
    )
    assert status == 200 and body["verify_cmd"] == "pytest -x --timeout=60"
    assert fake.verify_cmd_calls == [("g", "pytest -x --timeout=60")]


def test_verify_cmd_clear_passes_none_to_service(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    ret = {"goal_id": "g", "verify_cmd": None}
    fake = _FakeGoals(verify_cmd=ret)
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(
        http_mod.goal_verify_cmd,
        _req({"goal_id": "g"}, {"verify_cmd": None}),
    )
    assert status == 200 and body["verify_cmd"] is None
    assert fake.verify_cmd_calls == [("g", None)]


def test_verify_cmd_missing_key_is_400(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    fake = _FakeGoals(verify_cmd={"goal_id": "g", "verify_cmd": "x"})
    monkeypatch.setattr(http_mod, "goals", fake)
    status, body = _call(http_mod.goal_verify_cmd, _req({"goal_id": "g"}, {"wrong_key": "x"}))
    assert status == 400 and body["error"] == "verify_cmd_required"
    assert fake.verify_cmd_calls == []  # rejected before the service is touched


def test_verify_cmd_unknown_goal_is_404(monkeypatch):
    from devclaw.server.routes import goals as http_mod
    monkeypatch.setattr(http_mod, "goals", _FakeGoals(verify_cmd=KeyError("g")))
    status, body = _call(http_mod.goal_verify_cmd, _req({"goal_id": "g"}, {"verify_cmd": "x"}))
    assert status == 404 and body["error"] == "not_found"
