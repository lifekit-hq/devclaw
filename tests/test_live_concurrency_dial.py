"""The concurrency cap is a LIVE operator dial, not an import-time constant.

Tripwire class: the pause/brake machinery. The invariant is that an operator
can change backpressure on a running instance and have it take effect on the
next pump — without a restart, a redeploy, or SSH. Before this existed,
`DEVCLAW_MAX_CONCURRENT` was read once at import, so the only way to change it
was editing the VPS env file and redeploying; worse, compose did not forward
the variable at all, making that edit a silent no-op (fixed 2026-08-31).

The second half of the invariant matters as much as the first: this dial is
BACKPRESSURE, never a safety gate. It must never be able to wedge dispatch to
zero — stopping work is what set_operator_hold and the run window are for.
"""

import pytest

from devclaw.state_store import StateStore


@pytest.fixture()
def store(tmp_path):
    return StateStore(str(tmp_path / "devclaw.db"))


# ---- the store dial ---------------------------------------------------------

def test_absent_override_means_use_the_configured_default(store):
    """Absence is the signal for 'use the default' — never a stored copy of it,
    so changing DEVCLAW_MAX_CONCURRENT still moves the floor for an instance
    that has never set an override."""
    assert store.max_concurrent() is None


def test_override_round_trips_and_clears(store):
    store.set_max_concurrent(1)
    assert store.max_concurrent() == 1

    store.set_max_concurrent(7)
    assert store.max_concurrent() == 7

    store.set_max_concurrent(None)
    assert store.max_concurrent() is None, "None must CLEAR, not store a zero"


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "two"])
def test_a_cap_that_could_wedge_dispatch_is_rejected(store, bad):
    """0 would stop every launch while looking like a tuning choice. The dial
    refuses rather than silently becoming a stop button."""
    with pytest.raises(ValueError):
        store.set_max_concurrent(bad)
    assert store.max_concurrent() is None


@pytest.mark.parametrize("junk", ["", "  ", "abc", "0", "-3", "1.5"])
def test_corrupt_stored_value_degrades_to_the_default_not_to_zero(store, junk):
    """A hand-edited or half-written meta row must fall back to the default.
    Degrading to 0 would wedge dispatch — the exact silent stall this dial
    exists to prevent."""
    store.set_meta("max_concurrent", junk)

    assert store.max_concurrent() is None


# ---- the queue reads it live ------------------------------------------------

class _FakeStore:
    """Only what _effective_max_concurrent touches."""

    def __init__(self, value):
        self._value = value

    def max_concurrent(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def _queue_with(store_value):
    from devclaw.task_queue import TaskQueue

    q = TaskQueue.__new__(TaskQueue)  # no __init__: this resolver needs only _store
    q._store = _FakeStore(store_value)
    return q


def test_effective_cap_prefers_the_live_override_over_the_import_default(monkeypatch):
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 4)

    assert _queue_with(1)._effective_max_concurrent() == 1, (
        "the operator dial must beat the import-time default — otherwise it "
        "only takes effect on redeploy, which is the bug"
    )


def test_effective_cap_falls_back_to_the_default_when_unset(monkeypatch):
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 4)

    assert _queue_with(None)._effective_max_concurrent() == 4


def test_a_store_failure_degrades_to_the_default_and_never_wedges(monkeypatch):
    """Backpressure, not a safety gate: a control-plane hiccup must not stop
    all work. The real gates (operator hold, run window, quota pause) are
    checked before this and fail closed on their own terms."""
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 3)

    assert _queue_with(RuntimeError("db locked"))._effective_max_concurrent() == 3


def test_the_pump_resolves_the_cap_per_call_not_once(monkeypatch):
    """Changing the override between pumps changes the next pump's cap — the
    whole point. A resolver that cached would pass every test above and still
    require a restart in production."""
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 4)
    q = _queue_with(4)

    assert q._effective_max_concurrent() == 4
    q._store._value = 1
    assert q._effective_max_concurrent() == 1
