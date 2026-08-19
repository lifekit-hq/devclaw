"""Tranche 1 / PR5 — goal_steering rows are the source of truth for steering
consumption (``consumed_at IS NULL`` == unread), consumed by EXACT row id via
``GoalStore.transition(..., consume_steering=[...])`` atomically with the
decision the steering informed. ``inbox.md`` stays the human-readable mirror
AND a hand-append input, lazily ingested via a per-goal cursor.

Named regression tests, each with a one-line comment naming the failure class
it closes. See ``devclaw/goal/store.py`` (``_ingest_inbox`` / ``append_steering``
/ ``unread_steering_rows``), ``devclaw/goal/state.py`` (the ``goal_steering``
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
    row-backed, which ALSO bumps goal_status.version (the ingest-cursor
    write); a mid-tick append therefore makes the tick's OWN dispatch
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
    # follows, so inbox_cursor (the ingest cursor, unrelated to this
    # consume) isn't clobbered back to 0 by the write.
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


# ---- 4. lazy migration of a pre-PR5 inbox.md --------------------------------


def test_lazy_migration_of_pre_pr5_inbox(tmp_path):
    """Lazy migration: a goal whose stored inbox_ingest_cursor was the OLD
    consume cursor (pre-PR5) must not have its already-consumed history
    re-fed to the loop on the first post-upgrade ingest. Lines below the
    old cursor become CONSUMED rows (preserved for the record, never
    unread); only lines at/after the cursor are fresh. Idempotent: a second
    read changes nothing further."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    d = tmp_path / "g"
    d.mkdir(exist_ok=True)
    (d / "inbox.md").write_text(
        "- [denys 2026-01-01T00:00:00+00:00] line one\n"
        "- [denys 2026-01-01T00:00:01+00:00] line two\n"
        "- [denys 2026-01-01T00:00:02+00:00] line three\n"
        "- [denys 2026-01-01T00:00:03+00:00] line four\n"
        "- [denys 2026-01-01T00:00:04+00:00] line five\n"
    )
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing", inbox_cursor=3))

    rows = store.unread_steering_rows("g")
    assert len(rows) == 2
    assert "line four" in rows[0][1]
    assert "line five" in rows[1][1]

    # peek at the full table to confirm the 3 consumed / 2 unconsumed split
    with store._state._lock:
        peek = store._state._db.execute(
            "SELECT consumed_at FROM goal_steering WHERE goal_id = ? ORDER BY id", ("g",)
        ).fetchall()
    assert [row["consumed_at"] is not None for row in peek] == [True, True, True, False, False]

    # idempotent — a second read yields the same 2 unread rows
    rows2 = store.unread_steering_rows("g")
    assert [line for _, line in rows2] == [line for _, line in rows]


# ---- 5. mirror no-double-ingest ---------------------------------------------


def test_mirror_no_double_ingest(tmp_path):
    """append_steering's own cursor bump means the mirrored inbox.md lines
    are never re-ingested as a SECOND row by a later read. A genuinely NEW
    hand-typed line (appended to the file directly, bypassing
    append_steering) DOES show up — as exactly one additional 'manual'
    row."""
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_steering("g", ["ship the health check"], source="denys")

    rows = store.unread_steering_rows("g")
    assert len(rows) == 1 and "ship the health check" in rows[0][1]
    # calling again must not duplicate the mirrored line
    rows_again = store.unread_steering_rows("g")
    assert [line for _, line in rows_again] == [line for _, line in rows]

    # a genuinely hand-typed line, appended straight to the file
    with (tmp_path / "g" / "inbox.md").open("a") as fh:
        fh.write("- [denys 2026-01-02T00:00:00+00:00] hand-typed note\n")

    rows_after_hand_append = store.unread_steering_rows("g")
    lines = [line for _, line in rows_after_hand_append]
    # machine and hand rows alike hold the inbox.md line verbatim — both keep
    # the `- [source ts]` prefix the steering-rendering [auto-eval]-marker
    # contract relies on.
    assert len(lines) == 2
    assert "ship the health check" in lines[0] and lines[0].startswith("- [denys ")
    assert lines[1] == "- [denys 2026-01-02T00:00:00+00:00] hand-typed note"
    raw_rows = store._goal_state.unread_steering_rows("g")
    assert [r["source"] for r in raw_rows] == ["denys", "manual"]


# ---- 6. ingest tolerates cursor > file length -------------------------------


def test_ingest_tolerates_cursor_past_file_length(tmp_path):
    """The crash-ordering edge documented in GoalStore.append_steering's
    docstring: the ingest cursor ends up AHEAD of inbox.md's current line
    count (an operator clearing/truncating the file by hand is the simplest
    way to reach it). _ingest_inbox must not raise, go negative, or
    re-ingest anything — it's simply a no-op until the file catches back
    up, and existing unconsumed rows are untouched."""
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_steering("g", ["already ingested"], source="denys")
    assert store.load_status("g").inbox_cursor == 1

    # operator clears inbox.md by hand — the stored cursor is now AHEAD of
    # the (now-shorter) file.
    (tmp_path / "g" / "inbox.md").write_text("# g — inbox (steering)\n\n")

    rows = store.unread_steering_rows("g")  # must not raise / go negative
    assert len(rows) == 1 and "already ingested" in rows[0][1]  # the original row is untouched

    assert store.load_status("g").inbox_cursor == 1  # no new lines to account for — a no-op
