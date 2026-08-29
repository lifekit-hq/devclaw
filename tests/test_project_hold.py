"""The derived single-writer project hold — spec 010 P1.

The hold is a pure function of goal rows (FR-005, amended 2026-08-22): there is
no lock row to acquire, release, or leak. These tests pin the derivation itself;
the dispatch consequences live in test_goal_tick.py.
"""

from __future__ import annotations

from devclaw.goal import project_hold
from devclaw.goal.models import GoalStatus
from devclaw.goal.store import GoalStore
from tests.goal_fakes import Clock, seed_goal


def _store(tmp_path):
    return GoalStore(tmp_path, now=Clock())


def _seed(store, tmp_path, goal_id, *, workspace="/repos/alpha", phase="idle",
          project_id=None, created_at_ms=None):
    """Seed a goal. ``created_at_ms`` writes the "goal created" log row at an
    EXPLICIT timestamp — `append_log` stamps real wall-clock, so two seeds in
    one test can share a millisecond and silently reduce an age assertion to
    the id tie-break. Pass None to seed a goal with no log rows at all."""
    seed_goal(tmp_path, goal_id, workspace_dir=workspace, project_id=project_id)
    store.save_status(goal_id, GoalStatus(phase=phase, lifecycle="executing"))
    if created_at_ms is not None:
        # create_goal's first act — the age source the derivation orders by.
        store._goal_state.append_log_row(goal_id, "- [t] goal created", created_at_ms)


def test_holder_is_the_oldest_non_terminal_goal_on_the_project(tmp_path):
    """FR-001/FR-005: one holder per project, OLDEST first.

    The ids are chosen so alphabetical order CONTRADICTS age order: "aaa" is
    younger than "zzz", so a holder picked by id would be "aaa" and only a
    holder picked by age can be "zzz". Without that, the id tie-break would
    make this assertion pass whether or not age is consulted at all."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "zzz", created_at_ms=1_000)
    _seed(store, tmp_path, "aaa", created_at_ms=2_000)

    holders = project_hold.holder_map(store)

    assert holders["/repos/alpha"] == "zzz"


def test_undatable_goal_never_displaces_a_datable_one(tmp_path):
    """A goal with no log rows has no age. It must sort LAST, not first —
    otherwise a fixture (or a row-loss) would silently steal the project from
    the goal actually working it."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "dated", created_at_ms=5_000)
    _seed(store, tmp_path, "aaa-undated", created_at_ms=None)

    assert project_hold.holder_map(store)["/repos/alpha"] == "dated"


def test_holder_is_deterministic_when_ages_tie(tmp_path):
    """A tie must not be resolved by read order — goal id breaks it, so two
    writers computing the hold reach the same answer (the race-free property
    the derived form is chosen for)."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "bbb", created_at_ms=1_000)
    _seed(store, tmp_path, "aaa", created_at_ms=1_000)

    assert project_hold.holder_map(store)["/repos/alpha"] == "aaa"


def test_terminal_goals_never_hold_a_project(tmp_path):
    """FR-003 / the cancel edge case: a terminal goal drops out of the
    derivation, so the next goal becomes holder with nothing 'released'."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "first", created_at_ms=1_000)
    _seed(store, tmp_path, "second", created_at_ms=2_000)
    assert project_hold.holder_map(store)["/repos/alpha"] == "first"

    store.save_status("first", GoalStatus(phase="cancelled"))
    assert project_hold.holder_map(store)["/repos/alpha"] == "second"

    store.save_status("second", GoalStatus(phase="done"))
    assert "/repos/alpha" not in project_hold.holder_map(store)


def test_blocked_goal_is_skipped_in_holder_derivation(tmp_path):
    """Spec 025 FR-015 (skip-over) — REPLACES spec 010 FR-008's
    blocked-holder ruling and its test (symmetric ratchet): a blocked goal is
    not a holder candidate, so the queued successor takes the lane instead of
    idling behind a park only a human can clear. A scope whose every goal is
    blocked simply has no holder."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "first", phase="blocked", created_at_ms=1_000)
    _seed(store, tmp_path, "second", created_at_ms=2_000)

    assert project_hold.holder_map(store)["/repos/alpha"] == "second"

    store.save_status("second", GoalStatus(phase="blocked"))
    assert "/repos/alpha" not in project_hold.holder_map(store)


def test_in_flight_goal_outranks_age_as_holder(tmp_path):
    """The skip-over trap (spec 025): a resumed older predecessor must not
    reclaim the lane from a successor whose task is LIVE — in-flight work
    outranks age, so the second writer is impossible by derivation."""
    from devclaw.goal.models import InFlight

    store = _store(tmp_path)
    _seed(store, tmp_path, "older-resumed", created_at_ms=1_000)
    _seed(store, tmp_path, "younger-running", created_at_ms=2_000)
    store.save_status("younger-running", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "work"),
    ))

    assert project_hold.holder_map(store)["/repos/alpha"] == "younger-running"


def test_goals_on_distinct_projects_each_hold_their_own(tmp_path):
    """FR-001 / SC-005: the hold is per-project, never global."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "a1", workspace="/repos/alpha", created_at_ms=1_000)
    _seed(store, tmp_path, "b1", workspace="/repos/beta", created_at_ms=2_000)

    holders = project_hold.holder_map(store)

    assert holders["/repos/alpha"] == "a1"
    assert holders["/repos/beta"] == "b1"


def test_project_id_beats_workspace_dir_as_the_scope(tmp_path):
    """The registered reference key (#524 P3) is the project's identity when
    set: two goals sharing a project_id contend even if their checkouts differ."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "first", workspace="/repos/one", project_id="proj", created_at_ms=1_000)
    _seed(store, tmp_path, "second", workspace="/repos/two", project_id="proj", created_at_ms=2_000)

    holders = project_hold.holder_map(store)

    assert holders["proj"] == "first"
    assert "/repos/one" not in holders


def test_goal_without_a_project_scope_is_never_queued(tmp_path):
    """A goal with no project_id and no workspace contends for nothing —
    there is no shared repository to serialize."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "loner", workspace_dir="")
    store.save_status("loner", GoalStatus(phase="idle"))

    assert project_hold.scope_key(store.load_goal("loner")) is None
    assert project_hold.holder_map(store) == {}


def test_waiting_reason_names_the_holding_goal(tmp_path):
    """FR-002 / SC-006: the operator can identify the holder without log-diving."""
    reason = project_hold.waiting_reason("first")

    assert "first" in reason
    assert "automatically" in reason


def test_holder_map_survives_an_unloadable_goal(tmp_path):
    """A bad goal.yaml must not sink the sweep — the same rule tick_all already
    applies to its per-goal resolvers."""
    store = _store(tmp_path)
    _seed(store, tmp_path, "good", created_at_ms=1_000)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "goal.yaml").write_text("{{{ not yaml")

    holders = project_hold.holder_map(store)

    assert holders["/repos/alpha"] == "good"
