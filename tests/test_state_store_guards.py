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
    id1 = store.append_event(task_id="t1", program_id=None, type="ActionEvent", source="agent", payload_json="{}")
    id2 = store.append_event(task_id="t1", program_id=None, type="ObservationEvent", source="env", payload_json="{}")
    assert id2 > id1
    evs = store.list_events(task_id="t1")
    assert [e.type for e in evs] == ["ActionEvent", "ObservationEvent"]
    # resume cursor
    after = store.list_events(task_id="t1", since_id=id1)
    assert [e.id for e in after] == [id2]
