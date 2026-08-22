"""#616 — one migration, one cutoff: cognition rows carry ``response_text`` only.

Cutoff 2026-07-10 (T0.5, #193). Before it a cognition trace row stored only a
240-char ``response_preview``; telemetry carried a second read path for those
rows ever since. These tests pin the migration that makes the deletion safe
and the deletion itself.

A pre-cutoff row is simulated the way an upgrade actually looks: open the
database once (the migration stamps its marker), clear the marker, write rows
in the old shape, then reopen — which is exactly the code path a deployed
instance takes on the release that carries this change.
"""

from __future__ import annotations

import json

import pytest

from devclaw.state_store import StateStore
from devclaw.state_store import trace_migration as _mig


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "traces.db")


def _seed_pre_cutoff(db_path: str, payloads: "list[dict]") -> None:
    """Write ``payloads`` as cognition trace rows with the migration marker
    cleared, so the next :class:`StateStore` construction sweeps them."""
    store = StateStore(db_path)
    store.delete_meta(_mig.MIGRATION_META_KEY)
    for i, payload in enumerate(payloads):
        store.append_trace_event(
            trace_id=f"t{i}", goal_id="g", kind="cognition", payload=payload,
        )


def _payloads(store: StateStore) -> "list[dict]":
    return [r["payload"] for r in store.read_traces(goal_id="g")]


def test_pre_cutoff_preview_only_row_is_backfilled_into_response_text(db_path):
    verdict = json.dumps({"verdict": "on_track", "rationale": "r"})
    _seed_pre_cutoff(db_path, [{"role": "evaluator", "response_preview": verdict}])

    (payload,) = _payloads(StateStore(db_path))
    assert payload["response_text"] == verdict
    assert "response_preview" not in payload


def test_migration_never_overwrites_an_existing_response_text(db_path):
    """A post-cutoff row carries both fields, and the preview is the truncated
    one. The full text must win — folding the preview over it would be data
    loss dressed up as a migration."""
    full = json.dumps({"rationale": "x" * 400, "verdict": "achieved"})
    _seed_pre_cutoff(
        db_path,
        [{"role": "evaluator", "response_preview": full[:240], "response_text": full}],
    )

    (payload,) = _payloads(StateStore(db_path))
    assert payload["response_text"] == full
    assert "response_preview" not in payload


def test_migration_runs_once_and_leaves_later_rows_alone(db_path):
    _seed_pre_cutoff(db_path, [{"role": "evaluator", "response_preview": "old"}])
    store = StateStore(db_path)
    assert store.get_meta(_mig.MIGRATION_META_KEY)

    # A row written after the sweep with the old key is NOT swept again: the
    # marker, not any per-row condition, is what makes the migration one-shot.
    store.append_trace_event(
        trace_id="late", goal_id="g", kind="cognition",
        payload={"role": "evaluator", "response_preview": "written late"},
    )
    assert _mig.migrate_cognition_response_text_once(store, now_ms=1) == 0
    late = _payloads(StateStore(db_path))[1]
    assert late["response_preview"] == "written late"


def test_migration_resumes_when_the_marker_stamp_never_lands(db_path, monkeypatch):
    """Crash-safety: rows rewritten before the crash stay rewritten, the marker
    stays unset, and the next construction finishes the sweep and stamps."""
    _seed_pre_cutoff(db_path, [{"role": "evaluator", "response_preview": "a"}])
    store = _open_without_sweep(db_path, monkeypatch)

    with pytest.raises(RuntimeError):
        _mig.migrate_cognition_response_text_once(store, now_ms=1)
    assert store.get_meta(_mig.MIGRATION_META_KEY) is None
    (payload,) = _payloads(store)
    assert payload["response_text"] == "a"  # the row-level work survived

    monkeypatch.undo()
    reopened = StateStore(db_path)
    assert reopened.get_meta(_mig.MIGRATION_META_KEY)
    (payload,) = _payloads(reopened)
    assert payload["response_text"] == "a"
    assert "response_preview" not in payload


def _open_without_sweep(db_path: str, monkeypatch) -> StateStore:
    """A store whose construction-time sweep is a no-op and whose ``set_meta``
    then raises — the "crashed before the stamp" shape."""
    monkeypatch.setattr(
        "devclaw.state_store.core.migrate_cognition_response_text_once",
        lambda store, now_ms: 0,
    )
    store = StateStore(db_path)

    def _boom(key, value):
        raise RuntimeError("crash before the stamp")

    monkeypatch.setattr(store, "set_meta", _boom)
    return store


def test_torn_payload_row_is_skipped_without_wedging_the_sweep(db_path):
    """A non-JSON payload keeps matching the sweep's LIKE filter forever; the
    id cursor advances per row regardless, so the sweep terminates and the rows
    behind the torn one still migrate."""
    _seed_pre_cutoff(db_path, [{"role": "evaluator", "response_preview": "good"}])
    store = StateStore(db_path)
    store.delete_meta(_mig.MIGRATION_META_KEY)
    with store._lock:
        store._db.execute(
            "INSERT INTO traces (trace_id, goal_id, kind, ts, payload_json) "
            "VALUES ('torn', 'g', 'cognition', 1, '{\"response_preview\": ')"
        )
        store._db.commit()
    store.append_trace_event(
        trace_id="after", goal_id="g", kind="cognition",
        payload={"role": "evaluator", "response_preview": "behind the torn row"},
    )

    reopened = StateStore(db_path)
    assert reopened.get_meta(_mig.MIGRATION_META_KEY)
    with reopened._lock:
        rows = reopened._db.execute(
            "SELECT trace_id, payload_json FROM traces ORDER BY id"
        ).fetchall()
    by_trace = {r["trace_id"]: r["payload_json"] for r in rows}
    assert by_trace["torn"] == '{"response_preview": '  # left exactly as found
    assert json.loads(by_trace["after"])["response_text"] == "behind the torn row"
    assert json.loads(by_trace["t0"])["response_text"] == "good"


def test_sweep_walks_every_batch(db_path, monkeypatch):
    """The cursor must carry across batches — a sweep that only ever rewrote
    the first ``_BATCH`` rows would leave a long trace table half-migrated."""
    monkeypatch.setattr(_mig, "_BATCH", 2)
    _seed_pre_cutoff(
        db_path, [{"role": "evaluator", "response_preview": f"r{i}"} for i in range(5)]
    )

    payloads = _payloads(StateStore(db_path))
    assert [p["response_text"] for p in payloads] == [f"r{i}" for i in range(5)]
    assert not any("response_preview" in p for p in payloads)
