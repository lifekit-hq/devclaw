"""State store unit tests — busy-timeout pragma, atomic claim, event ordering.

(The program/DAG CRUD tests that used to live beside these died with the
lane's write half — spec 022 US3 demolition, completed by the prune PR.)
"""

import pytest

from devclaw.state_store import StateStore


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def test_busy_timeout_pragma_applied(store):
    from devclaw.state_store import SQLITE_BUSY_TIMEOUT_MS

    got = store._db.execute("PRAGMA busy_timeout").fetchone()[0]
    assert got == SQLITE_BUSY_TIMEOUT_MS
    assert got > 0  # wait for the lock instead of failing fast at 0


def test_claim_pending_is_atomic(store):
    store.create_task(id="t1", kind="fix_bug", workspace_dir="/ws", goal="g")
    assert store.claim_pending("t1") is True
    # second claim must lose the race
    assert store.claim_pending("t1") is False
    assert store.get_task("t1").status == "running"


def test_events_append_and_order(store):
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    id1 = store.append_event(task_id="t1", type="ActionEvent", source="agent", payload_json="{}")
    id2 = store.append_event(task_id="t1", type="ObservationEvent", source="env", payload_json="{}")
    assert id2 > id1
    evs = store.list_events(task_id="t1")
    assert [e.type for e in evs] == ["ActionEvent", "ObservationEvent"]
    # resume cursor
    after = store.list_events(task_id="t1", since_id=id1)
    assert [e.id for e in after] == [id2]


def test_event_ts_normalized_to_ms_and_never_immediately_prune_eligible(store):
    """Retention tripwire: the runner has emitted seconds-scale time.time()
    ts values; stored verbatim in the ms column they read as 1970 and the
    30-day ms-cutoff prune deletes them as ancient — a just-written event
    must be stored in ms and survive the prune, whatever scale the emitter
    used."""
    import time

    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    now_s = int(time.time())
    store.append_event(
        task_id="t1", type="ACPToolCallEvent",
        source="agent", payload_json="{}", ts=now_s,  # seconds-scale input
    )
    (ts_stored,) = store._db.execute("SELECT ts FROM events WHERE task_id='t1'").fetchone()
    assert ts_stored == now_s * 1000  # normalized to ms at the single writer
    # force a prune cycle NOW (no watermark yet) — the fresh event survives
    deleted = store.maybe_prune_events(now_ms=int(time.time() * 1000), retention_days=30)
    assert deleted == 0
    assert store.list_events(task_id="t1")
    # the schema-ensure repair is idempotent: re-running it never double-scales
    store._db.execute("UPDATE events SET ts = ts * 1000 WHERE ts > 0 AND ts < 1000000000000")
    (ts_after,) = store._db.execute("SELECT ts FROM events WHERE task_id='t1'").fetchone()
    assert ts_after == ts_stored


def test_task_result_compaction_touches_only_old_settled_rows(store):
    """Retention tripwire (settled-task transcript compaction): only settled
    rows past retention lose result_json; fresh settled rows and unsettled
    rows of ANY age keep theirs, error/pr_url survive compaction, and
    retention_days=0 disables the pass entirely."""
    import json as _json
    import time

    now = int(time.time() * 1000)
    old = now - 40 * 24 * 3600 * 1000
    db = store._db
    for tid, status, completed, result in (
        ("old-done", "done", old, '{"big": "transcript"}'),
        ("new-done", "done", now, '{"fresh": true}'),
        ("old-running", "running", None, '{"partial": true}'),
    ):
        db.execute(
            "INSERT INTO tasks (id, kind, status, workspace_dir, goal, created_at,"
            " completed_at, result_json, error, pr_url) "
            "VALUES (?, 'implement_feature', ?, '/ws', 'g', ?, ?, ?, 'boom', 'http://pr')",
            (tid, status, old, completed, result),
        )
    db.commit()

    # disabled → nothing happens, not even to ancient rows
    assert store.maybe_compact_task_results(now_ms=now, retention_days=0) == 0
    assert store.get_task("old-done").result_json is not None

    assert store.maybe_compact_task_results(now_ms=now, retention_days=30) == 1
    old_done = store.get_task("old-done")
    assert old_done.result_json is None
    assert old_done.error == "boom" and old_done.pr_url == "http://pr"  # summary survives
    assert _json.loads(store.get_task("new-done").result_json) == {"fresh": True}
    assert store.get_task("old-running").result_json is not None  # never touch unsettled
    # daily watermark: a second call in the same cycle is a no-op
    assert store.maybe_compact_task_results(now_ms=now, retention_days=30) == 0
