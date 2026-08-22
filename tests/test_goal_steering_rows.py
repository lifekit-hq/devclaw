"""Tranche 1 / PR5 — goal_steering rows are the source of truth for steering
consumption (``consumed_at IS NULL`` == unread), consumed by EXACT row id via
``GoalStore.transition(..., consume_steering=[...])`` atomically with the
decision the steering informed. ``inbox.md`` is a generated mirror ONLY: since
#617 it is never read back, and steering enters through ``steer_goal`` alone.

Named regression tests, each with a one-line comment naming the failure class
it closes. See ``devclaw/goal/store/content.py`` (``append_steering`` /
``unread_steering_rows``), ``devclaw/goal/store/view_migration.py`` (the
one-shot ingest of pre-#617 inbox.md content),
``devclaw/goal/state.py`` (the ``goal_steering``
row surface), and ``devclaw/goal/tick.py``'s ``_handle_long_lived_advance``
(exact-id capture + threading ``consume_steering`` into the dispatch
transition).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from devclaw.goal.models import GoalStatus
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from devclaw.goal.transitions import Event
from tests.goal_fakes import Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal


async def _tick(store, goal_id, evaluator, engine, notifier, *, prepare=fake_prepare):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=prepare,
    )


# ---- 1. steer-mid-tick not lost (THE headline test) ------------------------


@pytest.mark.asyncio
async def test_steer_mid_dispatch_not_lost(tmp_path):
    """THE headline regression (PR5, re-aimed at the thin path — there is no
    planner await anymore, but the tick still crosses async seams between its
    steering read and the dispatch commit: workspace prep, engine dispatch).
    Pre-PR5, unread steering was consumed by a COUNT-based cursor stamped at
    the end of the tick — a steer landing in that window was silently
    swallowed even though nothing ever acted on it. PR5 makes append_steering
    row-backed, and it explicitly bumps goal_status.version
    (GoalState.bump_status_version — #617 turned that from a side effect of
    the deleted ingest-cursor write into its own named one); a mid-tick
    append therefore makes the tick's OWN dispatch
    transition CAS-fail exactly like a concurrent steer_goal call
    (Outcome.CONFLICT) — the row rides the abandoned write, stays unread, and
    the NEXT tick dispatches with it in the advance brief."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    prep_calls = 0

    async def steer_mid_prep(ws, repo_url=None, branch=None):
        # models steer_goal landing between the steering read and the
        # dispatch commit — the prep await is the widest mid-tick seam
        nonlocal prep_calls
        prep_calls += 1
        store.append_steering("g", ["mid-tick correction"], source="denys")
        return branch or "main"

    out = await _tick(store, "g", evaluator, engine, notifier, prepare=steer_mid_prep)

    assert prep_calls == 1
    assert out is Outcome.CONFLICT  # the mid-tick write made this tick's own CAS stale
    assert evaluator.calls == 0     # the whole round is mechanism — zero cognition
    unread = store.unread_steering_rows("g")
    # rows store the mirror-formatted line (`- [denys <ts>] …`) so the
    # worker-visible text keeps its source marker — assert on the payload.
    assert len(unread) == 1 and "mid-tick correction" in unread[0][1]

    out2 = await _tick(store, "g", evaluator, engine, notifier)

    assert out2 is Outcome.DISPATCHED  # a dispatch fired — steering IS work
    action, _goal, _url = engine.dispatched[-1]
    assert "mid-tick correction" in action.goal  # rode into the advance brief
    assert store.unread_steering_rows("g") == []  # consumed atomically with the dispatch


# ---- 2. exact-id consumption ------------------------------------------------


def test_exact_id_consumption(tmp_path):
    """Exact-id consumption: two unread rows are read (their ids captured),
    the decision transition consumes precisely those ids; a THIRD row
    appended AFTER the read (simulating a steer landing after the tick
    already snapshotted what to consume) is untouched by that consume —
    it's picked up whole on the next read."""
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_steering("g", ["first", "second"], source="denys")

    rows = store.unread_steering_rows("g")
    assert len(rows) == 2
    assert "first" in rows[0][1] and "second" in rows[1][1]
    ids = [rid for rid, _ in rows]

    # a third steer lands AFTER the read captured its ids
    store.append_steering("g", ["third"], source="denys")

    status = store.load_status("g")
    # `new` MUST be built off the freshly-loaded status (replace(), never a
    # bare GoalStatus()) — same rule every production transition() call site
    # follows, so the third steer's version bump is respected rather than
    # clobbered by a stale-snapshot write.
    store.transition(
        "g", Event.RESUME_IDLE, replace(status, phase="idle", next="done"),
        expect=status, consume_steering=ids,
    )

    remaining = store.unread_steering_rows("g")
    assert len(remaining) == 1 and "third" in remaining[0][1]


# ---- 3. consumption is atomic with the decision write -----------------------


@pytest.mark.asyncio
async def test_consumption_atomic_with_decision_write(tmp_path):
    """Consumption rides the SAME CAS'd transaction as the dispatch write: a
    version bump mid-tick from something UNRELATED to steering (simulated
    via update_status_fields during the workspace-prep await, the same shape
    test_goal_transitions.py uses for "any writer can trigger
    TransitionConflict") makes the tick's dispatch transition CAS-fail — the
    steering rows it read stay unconsumed (they ride the abandoned write),
    and the NEXT tick re-dispatches with the SAME steering still visible."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_steering("g", ["do the thing"], source="denys")

    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    async def bump_mid_prep(ws, repo_url=None, branch=None):
        # any concurrent writer (the no-progress watchdog, another in-process
        # tick) landing between the steering read and the dispatch commit
        store.update_status_fields("g", last_tick_at=store.now_iso())
        return branch or "main"

    out = await _tick(store, "g", evaluator, engine, notifier, prepare=bump_mid_prep)

    assert out is Outcome.CONFLICT
    unread = store.unread_steering_rows("g")
    assert len(unread) == 1 and "do the thing" in unread[0][1]

    out2 = await _tick(store, "g", evaluator, engine, notifier)
    assert out2 is Outcome.DISPATCHED
    action, _goal, _url = engine.dispatched[-1]
    assert "do the thing" in action.goal   # the SAME steering, now applied
    assert store.unread_steering_rows("g") == []
    assert evaluator.calls == 0            # both rounds were zero-cognition


# ---- 4. the one-shot migration of a pre-#617 inbox.md ----------------------


def test_one_shot_migration_splits_pre_pr5_inbox_on_the_stored_cursor(tmp_path):
    """A goal whose stored inbox_ingest_cursor was the OLD consume cursor
    (pre-PR5) must not have its already-consumed history re-fed to the loop
    when #617's one-shot migration ingests its inbox.md. Lines below the old
    cursor become CONSUMED rows (preserved for the record, never unread);
    only lines at/after the cursor are fresh.

    The whole file must exist BEFORE the store is constructed — that is the
    #617 cutoff: the migration runs once, at construction, and nothing reads
    a view afterwards."""
    seed_goal(tmp_path, "g")
    d = tmp_path / "g"
    (d / "inbox.md").write_text(
        "- [denys 2026-01-01T00:00:00+00:00] line one\n"
        "- [denys 2026-01-01T00:00:01+00:00] line two\n"
        "- [denys 2026-01-01T00:00:02+00:00] line three\n"
        "- [denys 2026-01-01T00:00:03+00:00] line four\n"
        "- [denys 2026-01-01T00:00:04+00:00] line five\n"
    )
    (d / "STATUS.md").write_text(
        "---\nphase: idle\nlifecycle: executing\ninbox_cursor: 3\n---\n\nbody\n"
    )

    store = GoalStore(tmp_path, now=Clock())

    rows = store.unread_steering_rows("g")
    assert len(rows) == 2
    assert "line four" in rows[0][1]
    assert "line five" in rows[1][1]

    # the full table: 3 consumed history rows, 2 still unread
    with store._state._lock:
        peek = store._state._db.execute(
            "SELECT consumed_at FROM goal_steering WHERE goal_id = ? ORDER BY id", ("g",)
        ).fetchall()
    assert [row["consumed_at"] is not None for row in peek] == [True, True, True, False, False]

    # idempotent — a second read yields the same 2 unread rows
    rows2 = store.unread_steering_rows("g")
    assert [line for _, line in rows2] == [line for _, line in rows]


def test_one_shot_migration_tolerates_a_cursor_past_the_file_length(tmp_path):
    """The crash-ordering edge the old ingest documented: the stored cursor
    ends up AHEAD of inbox.md's line count (an operator truncating the file
    by hand is the simplest way to reach it). The migration must not raise or
    go negative — every surviving line is treated as already-consumed
    history, and nothing is offered to the planner as fresh steering."""
    seed_goal(tmp_path, "g")
    d = tmp_path / "g"
    (d / "inbox.md").write_text("- [denys 2026-01-01T00:00:00+00:00] the one surviving line\n")
    (d / "STATUS.md").write_text(
        "---\nphase: idle\nlifecycle: executing\ninbox_cursor: 9\n---\n\nbody\n"
    )

    store = GoalStore(tmp_path, now=Clock())

    assert store.unread_steering_rows("g") == []
    with store._state._lock:
        peek = store._state._db.execute(
            "SELECT consumed_at FROM goal_steering WHERE goal_id = ? ORDER BY id", ("g",)
        ).fetchall()
    assert [row["consumed_at"] is not None for row in peek] == [True]


# ---- 5. a hand-edited inbox.md is inert (#617) ------------------------------


def test_hand_edited_inbox_never_becomes_steering(tmp_path):
    """#617: ``inbox.md`` is a generated view, and a generated view is never
    read back for a decision. Before this, a line typed straight into the
    file was ingested into a ``goal_steering`` row on the next read — making
    whoever last touched the file a second writer to goal state, outside the
    CAS choke point that is supposed to make single-writer true.

    Appending to the file by hand must now change NOTHING the loop reads.
    Steering has exactly one door: ``steer_goal`` (which calls
    ``append_steering``)."""
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_steering("g", ["ship the health check"], source="denys")

    before = store.unread_steering_rows("g")
    assert len(before) == 1 and "ship the health check" in before[0][1]

    with (tmp_path / "g" / "inbox.md").open("a") as fh:
        fh.write("- [denys 2026-01-02T00:00:00+00:00] hand-typed note\n")

    after = store.unread_steering_rows("g")
    assert [line for _, line in after] == [line for _, line in before]
    assert not any("hand-typed" in line for _, line in after)
    raw_rows = store._goal_state.unread_steering_rows("g")
    assert [r["source"] for r in raw_rows] == ["denys"]


def test_append_steering_writes_the_row_before_its_mirror(tmp_path):
    """Ordering regression. With the ingest gone there is no re-ingestion to
    self-heal a mirror line that has no row behind it — such a line would be
    invisible to every decision reader while looking, to a human reading the
    file, exactly like real steering. So the row is written FIRST and the
    mirror second (the reverse of the pre-#617 order, which existed only to
    protect against an ingest cursor that no longer exists)."""
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))

    seen: list[bool] = []
    real_write = type(store)._dir

    class _Boom(RuntimeError):
        pass

    def exploding_dir(self, goal_id):
        # fires on append_steering's FIRST file touch, which is after the row
        # write; whether the row already exists is what this asserts.
        if seen:
            return real_write(self, goal_id)
        seen.append(True)
        raise _Boom()

    type(store)._dir = exploding_dir
    try:
        with pytest.raises(_Boom):
            store.append_steering("g", ["row must already exist"], source="denys")
    finally:
        type(store)._dir = real_write

    rows = store.unread_steering_rows("g")
    assert len(rows) == 1 and "row must already exist" in rows[0][1]



