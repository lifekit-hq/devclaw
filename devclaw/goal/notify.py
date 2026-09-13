"""Owner pings — POST ``{"text": ...}`` to the notify relay. Best-effort."""

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
        except Exception:  # noqa: BLE001 — never break the tick
            return False


class NullNotifier:
    async def send(self, text: str) -> bool:
        return False
