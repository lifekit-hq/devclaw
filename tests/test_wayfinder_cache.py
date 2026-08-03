"""P2 map-cache (docs/proposals/cognition-demolition.md): the control plane's
cached last-read wayfinder map — serialization round-trip + the row-only
goal_docs store seam (write/read/corrupt-fallback). No gh/network."""

from __future__ import annotations

import json

from devclaw.goal.store import GoalStore
from devclaw.goal.wayfinder import (
    WayfinderMap,
    WayfinderTicket,
    map_from_dict,
    map_to_dict,
)
from devclaw.state_store import _now_ms


def _sample_map():
    return WayfinderMap(
        map_number=1,
        destination="Ship /api/crons CRUD",
        notes="dotnet minimal API",
        out_of_scope=("auth", "the UI"),
        tickets=(
            WayfinderTicket(2, "record vs class?", "grilling", "closed",
                            resolution="record"),
            WayfinderTicket(3, "write endpoint", "task", "open", blocked_by=(2,)),
        ),
    )


# ---- serialization round-trip ----------------------------------------------


def test_map_dict_round_trip_is_identity():
    m = _sample_map()
    assert map_from_dict(map_to_dict(m)) == m  # frozen dataclasses compare by value


def test_map_json_round_trip_is_identity():
    m = _sample_map()
    assert map_from_dict(json.loads(json.dumps(map_to_dict(m)))) == m


def test_map_from_dict_tolerates_missing_fields():
    empty = map_from_dict({})
    assert empty.map_number == 0 and empty.destination == "" and empty.tickets == ()

    partial = map_from_dict({"map_number": 5, "tickets": [{"number": 9}]})
    assert partial.map_number == 5
    assert partial.tickets[0].number == 9
    assert partial.tickets[0].state == "open"  # default fills in


def test_map_from_dict_drops_non_dict_tickets():
    m = map_from_dict({"tickets": [{"number": 2}, "garbage", 7, None]})
    assert [t.number for t in m.tickets] == [2]


# ---- the goal_docs store cache ---------------------------------------------


def _store(tmp_path):
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir="/ws")
    return store


def test_cache_write_then_read_round_trips(tmp_path):
    store = _store(tmp_path)
    m = _sample_map()
    store.write_wayfinder_map("g", m)
    assert store.read_wayfinder_map("g") == m  # survives the goal_docs round-trip


def test_cache_read_none_when_nothing_cached(tmp_path):
    assert _store(tmp_path).read_wayfinder_map("g") is None


def test_cache_read_none_on_corrupt_row(tmp_path):
    # a garbled cache row degrades to None → the caller blocks legibly, never raises
    store = _store(tmp_path)
    store._goal_state.write_doc("g", "wayfinder_map", "not json{", _now_ms())
    assert store.read_wayfinder_map("g") is None


def test_cache_read_none_on_non_dict_json(tmp_path):
    store = _store(tmp_path)
    store._goal_state.write_doc("g", "wayfinder_map", "[1, 2, 3]", _now_ms())
    assert store.read_wayfinder_map("g") is None


def test_cache_overwrites_previous_snapshot(tmp_path):
    # one current snapshot per goal (PRIMARY KEY(goal_id, kind)) — a re-read
    # after a new map replaces, not appends
    store = _store(tmp_path)
    store.write_wayfinder_map("g", _sample_map())
    newer = WayfinderMap(map_number=1, destination="pivoted destination")
    store.write_wayfinder_map("g", newer)
    assert store.read_wayfinder_map("g") == newer
