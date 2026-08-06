"""Regression tests for §6 structured decision blocks — the STORE round-trip
(ADR 0010, P3.1a).

The per-tick planner that PARSED structured options at block time was cut in
demolition P3b (docs/proposals/cognition-demolition.md); the store methods
(``write_block_options`` / ``read_block_options``) survive and are what the
console reads to render click-to-steer buttons. These pin that round-trip.
"""

from __future__ import annotations

from devclaw.goal.models import BlockOption
from devclaw.goal.store import GoalStore
from tests.goal_fakes import seed_goal


def test_block_options_store_round_trip(tmp_path):
    seed_goal(tmp_path, "g1")
    store = GoalStore(tmp_path)
    store.write_block_options(
        "g1",
        [
            BlockOption(key="a", label="A", detail="d", steer="steer A"),
            BlockOption(key="b", label="B", steer="steer B"),
        ],
        "a",
    )
    got = store.read_block_options("g1")
    assert got["recommended"] == "a"
    assert [o["key"] for o in got["options"]] == ["a", "b"]
    assert got["options"][0]["steer"] == "steer A"


def test_read_block_options_none_when_unset(tmp_path):
    seed_goal(tmp_path, "g1")
    assert GoalStore(tmp_path).read_block_options("g1") is None


def test_write_empty_block_options_overwrites_stale(tmp_path):
    # A re-block with no enumerable options must clear a prior menu, not keep it.
    seed_goal(tmp_path, "g1")
    store = GoalStore(tmp_path)
    store.write_block_options("g1", [BlockOption(key="a", label="A", steer="s")], "a")
    store.write_block_options("g1", [], "")
    got = store.read_block_options("g1")
    assert got["options"] == []
    assert got["recommended"] == ""
