"""Regression tests for the merge queue (spec 010 US3, FR-102).

Fan-out lets two increments RUN at once; it must not let them LAND at once.
The properties pinned here are the ones that make that true:

  1. order is the PLAN's, not arrival's — lane 1 finishing first still waits;
  2. exactly one lane holds the shared target at a time;
  3. a slot is ALWAYS released — a failing lane must not wedge the lanes behind
     it, or one bad increment becomes a stuck goal;
  4. waiting is bounded and expires loud, so a lane that never arrives cannot
     silently hold its successors forever.

Pure asyncio — no git, no docker, no claude.
"""

from __future__ import annotations

import asyncio

import pytest

from devclaw.loom.merge_queue import MergeQueue, MergeQueueTimeout


async def test_lanes_integrate_strictly_in_plan_order_even_when_they_finish_out_of_order():
    q = MergeQueue()
    order: list = []
    started = asyncio.Event()

    async def lane(position: int, delay: float):
        await asyncio.sleep(delay)
        async with q.turn("goal/g1", position):
            order.append(position)
            started.set()

    # lane 1 is ready long before lane 0
    await asyncio.gather(lane(1, 0.0), lane(0, 0.05))
    assert order == [0, 1]


async def test_only_one_lane_integrates_at_a_time():
    q = MergeQueue()
    concurrent = 0
    peak = 0

    async def lane(position: int):
        nonlocal concurrent, peak
        async with q.turn("goal/g1", position):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1

    await asyncio.gather(*(lane(i) for i in range(4)))
    assert peak == 1


async def test_a_failed_lane_releases_its_slot_instead_of_wedging_the_queue():
    q = MergeQueue()
    landed: list = []

    async def lane(position: int):
        async with q.turn("goal/g1", position):
            landed.append(position)

    # lane 0 never takes a turn — it failed its gates and skipped
    await q.skip("goal/g1", 0)
    await asyncio.wait_for(asyncio.gather(lane(1), lane(2)), timeout=1)
    assert landed == [1, 2]


async def test_a_crashing_integration_still_advances_the_queue():
    """Fail closed, never wedge: the exception reaches the caller (so the lane
    fails) only AFTER the queue has released the slot."""
    q = MergeQueue()
    landed: list = []

    async def boom():
        async with q.turn("goal/g1", 0):
            raise RuntimeError("merge blew up")

    async def lane(position: int):
        async with q.turn("goal/g1", position):
            landed.append(position)

    with pytest.raises(RuntimeError):
        await boom()
    await asyncio.wait_for(lane(1), timeout=1)
    assert landed == [1]


async def test_goals_never_wait_on_each_other():
    """The key scopes the ordering: a lane of one goal must not be held up by an
    unrelated goal's queue."""
    q = MergeQueue()
    landed: list = []

    async def lane(key: str, position: int):
        async with q.turn(key, position):
            landed.append((key, position))

    # goal/a's lane 0 never arrives; goal/b must still land immediately
    await asyncio.wait_for(lane("goal/b", 0), timeout=1)
    assert landed == [("goal/b", 0)]


async def test_a_lane_that_never_arrives_expires_loud_instead_of_hanging():
    q = MergeQueue(wait_s=0.05)
    with pytest.raises(MergeQueueTimeout) as err:
        async with q.turn("goal/g1", 1):  # lane 0 never ran
            pass
    assert "waited" in str(err.value) and "goal/g1" in str(err.value)
    # …and it still released its own slot, so lane 2 is not stuck behind it
    async with q.turn("goal/g1", 2):
        pass
    assert q.position_now("goal/g1") == 3


async def test_a_repeated_position_never_rewinds_the_queue():
    """A re-run re-entering the same position must not re-admit an earlier lane."""
    q = MergeQueue()
    async with q.turn("goal/g1", 0):
        pass
    async with q.turn("goal/g1", 1):
        pass
    async with q.turn("goal/g1", 0):  # a retry of lane 0
        pass
    assert q.position_now("goal/g1") == 2
