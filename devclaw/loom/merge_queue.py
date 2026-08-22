"""The merge queue — serial integration of concurrently-executed increments.

Spec 010 US3 (FR-102). Fan-out lets two increments of one plan RUN at the same
time; it does not let them LAND at the same time. Integration onto the goal
branch is strictly serial and strictly in plan order — Bors' "not rocket science
rule", which is the whole of the theory here: never integrate a change against a
tree it was not tested against, and never integrate two at once.

The queue admits lane *k* only when every lane before it has finished with the
shared branch. Three properties make that safe rather than merely orderly:

* **Order is the plan's, not arrival's.** Lane 1 finishing first waits for lane
  0. Integration order is therefore reproducible, and a re-run of the same plan
  produces the same branch history.
* **A slot is always released.** Success, failure, or an exception inside the
  turn — the queue advances in a ``finally``. A lane that fails must not wedge
  the lanes behind it (that would turn one bad increment into a stuck goal).
* **Waiting is bounded.** A lane that never arrives (its task died between
  dispatch and integration) would otherwise hold its successors forever, so the
  wait has a ceiling and expires LOUD: the waiting lane fails with a reason
  naming what it waited for, rather than hanging silently.

Deliberately in-memory and per-process. The queue orders work that is *already
running in this process*; it is not durable state and must not become any. A
restart loses the ordering along with the running lanes themselves — the tasks
reset to pending and the whole group re-runs from the plan, which is the
recovery path that already exists (``TaskQueue.recover``). Persisting a position
counter would add a second source of truth about work that no longer exists.

Zero LLM, zero I/O, no store access: pure asyncio bookkeeping.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

#: Ceiling on how long one lane waits for its predecessors before failing loud.
#: Generous — a legitimate predecessor is a whole sandboxed agent run plus its
#: gates — but finite, because "wait forever" is how a lost lane becomes a wedged
#: goal (constitution VI).
DEFAULT_WAIT_S = 3 * 60 * 60


class MergeQueueTimeout(RuntimeError):
    """A lane waited past the ceiling for its turn. The lane fails; the queue
    keeps its ordering (this lane still releases its own slot on the way out)."""


class MergeQueue:
    """Serial, in-order admission to a shared integration target.

    ``key`` scopes the ordering — in devclaw it is the goal branch, so lanes of
    different goals never wait on each other. ``position`` is the lane's index in
    the plan, which is what makes the order the plan's rather than the race's.
    """

    def __init__(self, *, wait_s: float = DEFAULT_WAIT_S) -> None:
        self._wait_s = wait_s
        self._next: "dict[str, int]" = {}
        self._conds: "dict[str, asyncio.Condition]" = {}

    def _cond(self, key: str) -> asyncio.Condition:
        cond = self._conds.get(key)
        if cond is None:
            cond = asyncio.Condition()
            self._conds[key] = cond
        return cond

    def position_now(self, key: str) -> int:
        """The lane index the queue would admit next. Test/observability read."""
        return self._next.get(key, 0)

    async def _advance(self, key: str, position: int) -> None:
        cond = self._cond(key)
        async with cond:
            # Monotonic: a lane never rewinds the queue, so a duplicate release
            # (a retry re-entering the same position) is a no-op rather than a
            # way to admit an earlier lane twice.
            self._next[key] = max(self._next.get(key, 0), position + 1)
            cond.notify_all()

    async def skip(self, key: str, position: int) -> None:
        """Release a lane's slot without integrating — the lane failed its gates,
        or never had anything to land. Later lanes proceed immediately."""
        await self._advance(key, position)

    @asynccontextmanager
    async def turn(self, key: str, position: int):
        """Hold the shared target for exactly this lane, in plan order.

        Yields once every earlier lane has released. The slot is released on the
        way out whatever happened inside — an exception propagates to the caller
        AFTER the queue has advanced, so a failing integration fails its own lane
        and only its own lane."""
        cond = self._cond(key)
        try:
            async with cond:
                try:
                    await asyncio.wait_for(
                        cond.wait_for(lambda: self._next.get(key, 0) >= position),
                        timeout=self._wait_s,
                    )
                except asyncio.TimeoutError as err:
                    raise MergeQueueTimeout(
                        f"merge queue: lane {position} waited "
                        f"{self._wait_s:.0f}s for lane {self._next.get(key, 0)} on "
                        f"'{key}' and gave up — the earlier increment never "
                        f"reached integration"
                    ) from err
            yield
        finally:
            await self._advance(key, position)
