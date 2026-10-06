"""Tripwire: arming a self-deploy is idempotent per sha. A late push-to-main
arm job must not re-arm a sha that is already running or already deployed —
that would restart the instance twice for one merge."""
from __future__ import annotations

import asyncio

import pytest

from devclaw.server.routes import control


class _Request:
    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body


class _Store:
    def __init__(self, last) -> None:
        self._last = last
        self._pending = None

    def deploy_last(self):
        return self._last

    def deploy_pending(self):
        return self._pending

    def set_deploy_pending(self, *, sha, goal_id, since_ms) -> None:
        self._pending = (sha, goal_id, since_ms)


def _arm(monkeypatch, store, sha: str) -> None:
    monkeypatch.setattr(control, "store", store)
    monkeypatch.setenv("DEVCLAW_GIT_SHA", "run1")
    asyncio.run(control.control_deploy_pending(_Request({"sha": sha})))


@pytest.mark.parametrize("last,sha", [
    (None, "run1"),
    ({"sha": "new2", "outcome": "triggered"}, "new2"),
])
def test_a_late_arm_for_a_deployed_sha_is_ignored(monkeypatch, last, sha) -> None:
    store = _Store(last)
    _arm(monkeypatch, store, sha)
    assert store.deploy_pending() is None


@pytest.mark.parametrize("last,sha", [
    (None, "new2"),
    ({"sha": "new2", "outcome": "expired"}, "new2"),
    ({"sha": "new2", "outcome": "triggered"}, "new3"),
])
def test_a_new_or_unfired_sha_is_armed(monkeypatch, last, sha) -> None:
    store = _Store(last)
    _arm(monkeypatch, store, sha)
    assert store.deploy_pending()[0] == sha
