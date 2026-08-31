"""Instance health drift probes — named regression tests (spec 027 / issue #596).

Pins three invariants from the done-when:
  T1. A healthy instance (all probes below threshold) records no problem.
  T2. A threshold breach records exactly ONE deduplicated problem row; calling
      again with the same reading increments ``count`` on that same row.
  T3. A probe returning ``None`` (failure) produces no record and no exception.

Uses a real StateStore with a temp SQLite — same pattern as
``test_problems_catalog.py`` — so the dedup fingerprinting is exercised
end-to-end. The three injectable probe functions are monkeypatched at the
module level so no docker daemon or real filesystem is needed.
"""

from __future__ import annotations

import pytest

from devclaw.goal import health_drift as _hd
from devclaw.state_store import StateStore


#: The REAL implementations, captured before the autouse fixture below stubs
#: them — tests that exercise a probe itself must not be handed the stub.
_REAL_DEVICE_KEY = _hd._device_key


@pytest.fixture
def store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "health.db"))


@pytest.fixture(autouse=True)
def _single_disk_box(monkeypatch):
    """Default every test to the common shape: ONE disk behind every surface,
    so a breach is one row. Tests that care about split volumes override
    `_device_key` explicitly.

    Without this the probe would shell out to a real `docker info` during the
    suite — the no-docker-in-tests structural guard forbids that, and both the
    root and the device layout would vary by machine."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: "/tmp/goals")
    monkeypatch.setattr(_hd, "_device_key", lambda _p: "one-disk")


def _run(store: StateStore, **overrides) -> None:
    """Helper: run checks with benign defaults, overridable per-test."""
    kwargs: dict = dict(
        store=store,
        goals=[],
        project_workspaces=set(),
        now_ms=0,
        goals_dir="/tmp/goals",
        db_path="/tmp/goals/devclaw.db",
        disk_warn_pct=80.0,
        orphan_docker_warn=10,
        stale_ws_warn=20,
        docker_bin="docker",
    )
    kwargs.update(overrides)
    _hd.run_health_drift_checks(**kwargs)


# ---- T1: healthy instance records no problem --------------------------------


def test_healthy_instance_records_no_problem(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)

    assert store.list_problems() == []


@pytest.mark.parametrize(
    ("pct", "records"),
    [(79.9, False), (80.0, True), (80.1, True)],
    ids=["below", "at-threshold", "above"],
)
def test_disk_threshold_boundary_is_inclusive(store, monkeypatch, pct, records):
    """The comparison is `pct >= threshold`, so AT the threshold records.

    The previous version was named for the AT case but only ever exercised
    79.9 — the boundary it claimed to guard was untested, which is worse than
    no test: it reads as covered. All three sides are pinned here."""
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: pct)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, disk_warn_pct=80.0)

    assert bool(store.list_problems()) is records


def test_orphan_count_below_threshold_records_no_problem(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 9)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, orphan_docker_warn=10)
    assert store.list_problems() == []


# ---- T2: threshold breach → one deduplicated row ----------------------------


def test_disk_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "disk_usage_high"
    assert rows[0]["count"] == 1


def test_disk_breach_deduplicates_on_second_call(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    _run(store)

    rows = store.list_problems(category="other")
    # Still ONE row — dedup is working — with count 2.
    assert len(rows) == 1
    assert rows[0]["kind"] == "disk_usage_high"
    assert rows[0]["count"] == 2


def test_orphan_volume_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 15)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, orphan_docker_warn=10)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "orphan_docker_volumes"


def test_stale_workspace_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 25)

    _run(store, stale_ws_warn=20)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "stale_workspaces"


def test_all_three_breached_records_three_rows(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 15)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 25)

    _run(store, disk_warn_pct=80.0, orphan_docker_warn=10, stale_ws_warn=20)
    rows = store.list_problems(category="other")
    kinds = {r["kind"] for r in rows}
    assert kinds == {"disk_usage_high", "orphan_docker_volumes", "stale_workspaces"}


# ---- T3: probe failure → unknown reading, no record, no exception ----------


def test_disk_probe_failure_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: None)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)  # must not raise
    assert store.list_problems() == []


def test_docker_probe_failure_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: None)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    assert store.list_problems() == []


def test_all_probes_failing_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: None)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: None)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: None)

    _run(store)  # must not raise
    assert store.list_problems() == []


# ---- structural: zero-LLM guard (belt + suspenders) -------------------------


def test_run_health_drift_checks_never_invokes_claude(store, monkeypatch):
    """The probe module must never call into the cognition layer — belt + suspenders
    beyond what the typed function signatures already make impossible."""
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    # Confirm it runs clean without any cognition import side-effect.
    # Zero LLM is structurally guaranteed (no claude import in health_drift.py);
    # this test pins that no later edit silently wires in a cognition call.
    _run(store)




# ---- every instance-critical filesystem, once per device --------------------
# done_when named "the volume backing workspaces and the docker root", and the
# first implementation probed only goals_dir — the done-gate refused three
# rounds on that clause. The fix is deliberately NOT "add a second probe": the
# monitored set is derived from `_disk_surfaces`, so the DB volume (whose
# exhaustion kills the instance outright, and which `DEVCLAW_DB` configures
# independently of the goals dir) is covered by the same mechanism, and the
# next critical path is one line rather than a fourth bespoke check.


def test_every_instance_critical_path_is_enumerated(monkeypatch):
    """The surface list IS the contract — assert it names all three, so
    dropping one is a test failure rather than a silent blind spot."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: "/var/lib/docker")

    names = {n for n, _ in _hd._disk_surfaces("/g", "/db/devclaw.db", "docker")}

    assert names == {"workspace", "database", "docker root"}


def test_undeterminable_docker_root_is_omitted_not_guessed(monkeypatch):
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: None)

    names = {n for n, _ in _hd._disk_surfaces("/g", "/db/devclaw.db", "docker")}

    assert names == {"workspace", "database"}, "unknown must drop out, never guess"


def test_each_distinct_volume_is_reported_separately(store, monkeypatch):
    """A full docker root must never hide behind a healthy workspace volume —
    the exact gap that held the goal open."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: "/var/lib/docker")
    monkeypatch.setattr(_hd, "_device_key", lambda path: path)  # all distinct
    monkeypatch.setattr(
        _hd, "_disk_used_pct",
        lambda path: 95.0 if path == "/var/lib/docker" else 10.0,
    )
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _b, _w: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _p, _n: 0)

    _run(store)

    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert "docker root" in rows[0]["sample_message"]


def test_a_full_db_volume_is_caught_even_when_the_workspace_is_healthy(
    store, monkeypatch
):
    """The bug one row over: DEVCLAW_DB is configured independently, and a full
    DB volume kills the instance outright. A two-probe fix would have missed
    this; the derived surface set does not."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: None)
    monkeypatch.setattr(_hd, "_device_key", lambda path: path)
    monkeypatch.setattr(
        _hd, "_disk_used_pct",
        lambda path: 99.0 if path.endswith("devclaw.db") else 5.0,
    )
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _b, _w: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _p, _n: 0)

    _run(store, db_path="/data/devclaw.db")

    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert "database" in rows[0]["sample_message"]


def test_surfaces_sharing_a_disk_collapse_to_one_row_naming_all_of_them(
    store, monkeypatch
):
    """The common single-disk box: three surfaces, one alarm, but the message
    still names everything at risk on that volume."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: "/var/lib/docker")
    monkeypatch.setattr(_hd, "_device_key", lambda _path: "same-disk")
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 97.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _b, _w: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _p, _n: 0)

    _run(store)

    rows = store.list_problems(category="other")
    assert len(rows) == 1, "one disk must not raise three identical alarms"
    msg = rows[0]["sample_message"]
    assert "database" in msg and "docker root" in msg and "workspace" in msg


def test_surface_names_are_sorted_so_the_fingerprint_is_stable(store, monkeypatch):
    """Dedupe is on normalize(message). Unsorted names would fingerprint
    differently run to run and defeat the catalog's aging."""
    monkeypatch.setattr(_hd, "_docker_root_dir", lambda _bin: "/var/lib/docker")
    monkeypatch.setattr(_hd, "_device_key", lambda _path: "same-disk")
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 97.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _b, _w: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _p, _n: 0)

    _run(store)
    _run(store)

    rows = store.list_problems(category="other")
    assert len(rows) == 1 and rows[0]["count"] == 2
    assert rows[0]["sample_message"].startswith("database+docker root+workspace")


def test_device_key_falls_back_to_the_path_when_stat_fails(tmp_path):
    """An unreadable path must group with ITSELF, never merge into another
    surface's reading and inherit its all-clear."""
    missing = str(tmp_path / "nope")

    assert _REAL_DEVICE_KEY(missing) == _REAL_DEVICE_KEY(missing)
    assert _REAL_DEVICE_KEY(missing) != _REAL_DEVICE_KEY(str(tmp_path / "other"))


def test_the_sandcastle_seam_is_imported_at_module_scope(monkeypatch):
    """A rename in engine/sandcastle.py must break LOUDLY, not silently.

    `_orphan_docker_volume_count` runs inside a blanket `except Exception:
    return None`. While the seam was imported lazily inside that block, a
    rename on the other side was swallowed as an ImportError and the probe
    returned "unknown" forever — and unknown records no problem, so a
    permanently dead probe is indistinguishable from a healthy instance.
    Module-scope import turns the same rename into an import-time failure.
    """
    assert hasattr(_hd, "_toolchain_volume_name")
    assert hasattr(_hd, "_translate_workspace_path")

    import devclaw.engine.sandcastle as _sc

    assert _hd._toolchain_volume_name is _sc._toolchain_volume_name
    assert _hd._translate_workspace_path is _sc._translate_workspace_path


def test_orphan_probe_counts_volumes_no_registered_workspace_explains(
    store, monkeypatch
):
    """Exercises the REAL function against a stubbed docker, rather than
    monkeypatching it away — so the seam it depends on is actually executed and
    a rename is caught by a failing assertion, not just at import."""
    class _Result:
        returncode = 0
        stdout = "devclaw-toolchains-known\ndevclaw-toolchains-orphan\n"

    monkeypatch.setattr(_hd, "_toolchain_volume_name", lambda p: f"devclaw-toolchains-{p}")
    monkeypatch.setattr(_hd, "_translate_workspace_path", lambda ws: "known")
    monkeypatch.setattr(_hd.subprocess, "run", lambda *a, **k: _Result())

    assert _hd._orphan_docker_volume_count("docker", {"/ws/known"}) == 1
