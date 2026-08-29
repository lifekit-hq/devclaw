"""Outbound goal-layer progress — POST to the notify-relay, which fans out to
Telegram. Folded in from goalclaw.

The relay is the same container devclaw's task-level notify_url callbacks hit; the
goal layer posts free text to its ``/text`` passthrough. We POST ``{"text": ...}``
and treat any 2xx as sent. Notify is best-effort: a relay outage must never crash
a tick.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class Notifier(Protocol):
    async def send(self, text: str) -> bool: ...


class HttpNotifier:
    def __init__(self, url: str, timeout_s: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout_s

    async def send(self, text: str) -> bool:
        if not self._url:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json={"text": text})
            return resp.is_success
        except Exception:  # noqa: BLE001 — best-effort; never break the tick
            return False


class NullNotifier:
    """No-op notifier (notify disabled / tests)."""

    async def send(self, text: str) -> bool:  # noqa: D401
        return False


class QuietNotifier:
    """Quiet-mode decorator (spec 025 US3) around any :class:`Notifier`,
    bound at the ONE real choke point — GoalService's notifier binding — so it
    covers both the tick-path ``_notify`` sends and the cycle report's direct
    send. While armed, ``send`` suppresses-and-records; ``send_critical``
    (the instance-dead class: an auth pause a re-probe can't heal, a failed
    deploy rollback) always goes out. Expiry disarms lazily on the next send —
    a forgotten toggle can't mute the instance forever.

    ``state`` is the shared StateStore (quiet_mode meta + suppressed_pings);
    ``now_ms`` is injectable for tests. Storage trouble degrades to SENDING —
    a broken quiet-mode read must never silently eat a real ping.
    """

    def __init__(self, inner: Notifier, state, *, now_ms=None) -> None:
        self._inner = inner
        self._state = state
        self._now_ms = now_ms

    def _clock(self) -> int:
        if self._now_ms is not None:
            return int(self._now_ms())
        import time

        return int(time.time() * 1000)

    def _armed(self) -> bool:
        try:
            armed, until = self._state.quiet_mode()
            if not armed:
                return False
            if until is not None and self._clock() > until:
                self._state.set_quiet_mode(False)  # lazy self-disarm on expiry
                return False
            return True
        except Exception:  # noqa: BLE001 — degrade to sending, never to silence
            return False

    async def send(self, text: str) -> bool:
        if self._armed():
            try:
                self._state.record_suppressed_ping(text, self._clock())
                return True
            except Exception:  # noqa: BLE001 — a failed record must not eat the ping
                pass
        return await self._inner.send(text)

    async def send_critical(self, text: str) -> bool:
        return await self._inner.send(text)
