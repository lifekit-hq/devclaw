"""Tranche 1 / PR6 — log.md / deliveries.md move onto ``goal_log`` /
``goal_deliveries`` rows, with the files as generated mirrors (same pattern
PR3's STATUS.md and PR5's steering rows use).
``append_delivery`` gains a nullable ``ref_id`` idempotency key —
``UNIQUE(goal_id, ref_id)`` + INSERT OR IGNORE — closing a PR4-review nuance:
a ``TransitionConflict`` landing in the settle-retry window could make the
tick's retry append the SAME delivery twice.

Named regression tests, each with a one-line comment naming the failure class
it closes. See ``devclaw/goal/store.py`` (log/deliveries sections),
``devclaw/goal/state.py`` (the ``goal_log`` / ``goal_deliveries`` row
surface), and ``devclaw/goal/tick.py``'s ``_resolve_polling_action`` (the
``ref_id=ref.id`` call-site fix). The checklist/firmed-draft ``goal_docs``
round-trips this module used to cover were amputated with host cognition
(spec 008 shrink)."""

from __future__ import annotations

import pytest

from devclaw.goal.state import GoalState
from devclaw.goal.store import GoalStore
from devclaw.state_store import StateStore
from tests.goal_fakes import Clock, seed_goal


def _peek_log_row_count(store: GoalStore, goal_id: str) -> int:
    with store._state._lock:
        row = store._state._db.execute(
            "SELECT COUNT(*) AS n FROM goal_log WHERE goal_id = ?", (goal_id,)
        ).fetchone()
    return row["n"]


def _peek_delivery_row_count(store: GoalStore, goal_id: str) -> int:
    with store._state._lock:
        row = store._state._db.execute(
            "SELECT COUNT(*) AS n FROM goal_deliveries WHERE goal_id = ?", (goal_id,)
        ).fetchone()
    return row["n"]


# ---- 1. duplicate delivery is impossible (THE headline test) --------------


def test_duplicate_delivery_is_impossible(tmp_path):
    """The PR4-review nuance this PR closes: a settle re-run against the SAME
    in-flight ref (e.g. a TransitionConflict landing in the settle window,
    then the tick's retry re-executing the settle) must not double-record the
    delivery. Two calls with the same ref_id yield ONE row, ONE '## [' section
    in deliveries.md, and recent_deliveries shows it exactly once."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")

    store.append_delivery("g", "add /health", "PR: #7\nVerify: PASSED", ref_id="t-1")
    store.append_delivery("g", "add /health", "PR: #7\nVerify: PASSED", ref_id="t-1")

    assert _peek_delivery_row_count(store, "g") == 1
    deliveries_md = (tmp_path / "g" / "deliveries.md").read_text()
    assert deliveries_md.count("## [") == 1
    assert deliveries_md.count("add /health") == 1
    recent = store.recent_deliveries("g")
    assert recent.count("## [") == 1
    assert recent.count("add /health") == 1

    # A DIFFERENT ref_id is a genuinely new delivery — not swallowed by the
    # dedupe key.
    store.append_delivery("g", "add logging", "PR: #8", ref_id="t-2")
    assert _peek_delivery_row_count(store, "g") == 2
    assert (tmp_path / "g" / "deliveries.md").read_text().count("## [") == 2

    # ref_id is REQUIRED since the #616 cutoff. It used to default to None,
    # which took an unconditional-insert path — so identical content filed
    # twice produced two rows, and the "idempotent delivery" guarantee
    # silently did not apply to any caller that forgot the key. There is no
    # such caller now, by construction.
    with pytest.raises(TypeError):
        store.append_delivery("g", "unrelated work", "PR: #9")
    assert _peek_delivery_row_count(store, "g") == 2


# ---- 2. byte-parity on migration (log) -------------------------------------


def test_log_byte_parity_on_migration(tmp_path):
    """A pre-#617 log.md (no goal_log rows) reads back byte-identical through
    the row-backed recent_log — computed against a reference derived straight
    from the file content, the same filter the pre-PR6 file-tail read used
    (lines starting '- ['). The stray non-'- [' line proves the filter still
    applies post-migration. The file must be in place BEFORE the store is
    constructed: since #617 the ingest happens once, at construction, and no
    read ever touches log.md again."""
    seed_goal(tmp_path, "g")
    d = tmp_path / "g"
    d.mkdir(exist_ok=True)
    lines = [
        "- [2026-01-01T00:00:00+00:00] first",
        "- [2026-01-01T00:00:01+00:00] second",
        "- [2026-01-01T00:00:02+00:00] third",
        "not-a-log-line — should be filtered out",
        "- [2026-01-01T00:00:03+00:00] fourth",
        "- [2026-01-01T00:00:04+00:00] fifth",
    ]
    (d / "log.md").write_text("# g — log\n\n" + "\n".join(lines) + "\n")

    store = GoalStore(tmp_path, now=Clock())   # the one-shot migration runs HERE

    # Reference computed directly from the file — exactly the pre-PR6 read.
    ref_lines = [ln for ln in lines if ln.startswith("- [")]
    expected_recent = "\n".join(ref_lines[-3:])

    assert store.recent_log("g", n=3) == expected_recent
    # log_contains-equivalent checks (PR8 retired log_contains — adapted onto
    # recent_log with n wide enough to cover all 5 real rows).
    full_log = store.recent_log("g", n=10)
    assert "third" in full_log
    assert "not-a-log-line" not in full_log  # filtered, never ingested
    assert "nonexistent needle" not in full_log

    assert _peek_log_row_count(store, "g") == 5  # the 5 real log lines, not the stray one

    # No read re-ingests: appending to log.md by hand changes nothing.
    with (d / "log.md").open("a") as fh:
        fh.write("- [2026-01-01T00:00:05+00:00] hand-typed sixth\n")
    assert "hand-typed sixth" not in store.recent_log("g", n=10)
    assert _peek_log_row_count(store, "g") == 5


# ---- 3. byte-parity on migration (deliveries) ------------------------------


def test_deliveries_byte_parity_on_migration(tmp_path):
    """A pre-#617 deliveries.md (no goal_deliveries rows) reads back
    byte-identical through recent_deliveries — including a SMALL chars bound
    that slices mid-text, matching the pre-PR6 ``text[-chars:]`` behavior
    exactly. Written before construction: the ingest is one-shot (#617)."""
    seed_goal(tmp_path, "g")
    d = tmp_path / "g"
    d.mkdir(exist_ok=True)
    header = "# g — deliveries (what each action shipped)\n\n"
    section1 = "## [2026-01-01T00:00:00+00:00] add /health\n\nPR: #7\nVerify: PASSED\n\n"
    section2 = "## [2026-01-01T00:00:01+00:00] add logging\n\nPR: #8\n\n"
    legacy_text = header + section1 + section2
    (d / "deliveries.md").write_text(legacy_text)

    store = GoalStore(tmp_path, now=Clock())   # the one-shot migration runs HERE

    assert store.recent_deliveries("g") == legacy_text  # under the default 8000-char bound

    # A tiny char bound must slice mid-text identically to the old file read.
    small = 30
    expected_small = legacy_text[-small:]
    assert store.recent_deliveries("g", chars=small) == expected_small
    assert len(expected_small) < len(legacy_text)  # confirm the bound actually bites

    assert _peek_delivery_row_count(store, "g") == 2  # one row per '## [' section

    # Idempotent — a second read doesn't re-ingest / duplicate rows.
    store.recent_deliveries("g")
    assert _peek_delivery_row_count(store, "g") == 2



# ---- 6. log append keeps mirror + rows in lockstep -------------------------


def test_log_append_keeps_mirror_and_rows_in_lockstep(tmp_path):
    """After several appends, log.md's content equals the header plus the
    joined row messages — the file mirror and the row-backed source of truth
    never drift apart on the normal (non-crash) append path."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    store.append_log("g", "first")
    store.append_log("g", "second")
    store.append_log("g", "third")

    with store._state._lock:
        rows = store._state._db.execute(
            "SELECT message FROM goal_log WHERE goal_id = ? ORDER BY id ASC", ("g",)
        ).fetchall()
    messages = [r["message"] for r in rows]
    assert len(messages) == 3

    expected = "# g — log\n\n" + "".join(f"{m}\n" for m in messages)
    assert (tmp_path / "g" / "log.md").read_text() == expected


# ---- deliveries idempotency survives the file-mirror skip on ignore -------


def test_the_cutoff_rebuilds_deliveries_with_a_not_null_ref_id(tmp_path):
    """The #616 cutoff, and a pointed instance of what it is for.

    ``goal_deliveries.ref_id`` has been NOT NULL (PR2), then NULLABLE (PR6, so
    pre-idempotency-key sections could be ingested), and is NOT NULL again
    now. Three shapes for one column is exactly the "three overlapping
    histories" tax #611 names — and the nullable middle was actively harmful:
    SQLite treats every NULL as distinct, so ``UNIQUE(goal_id, ref_id)``
    silently did not constrain the rows that most needed it.

    A database carrying the nullable schema with a NULL row must come out of
    the cutoff with that row backfilled to a deterministic id, the column NOT
    NULL, and a NULL insert now refused."""
    import sqlite3

    from devclaw.goal.store.legacy_cutoff import apply_legacy_cutoff

    db_path = str(tmp_path / "devclaw.db")
    store = StateStore(db_path)
    with store._lock:
        store._db.execute("DROP TABLE IF EXISTS goal_deliveries")
        store._db.execute(
            """
            CREATE TABLE goal_deliveries (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              goal_id     TEXT NOT NULL,
              ref_id      TEXT,
              instruction TEXT,
              body        TEXT,
              created_at  INTEGER NOT NULL,
              UNIQUE(goal_id, ref_id)
            )
            """
        )
        store._db.executemany(
            "INSERT INTO goal_deliveries (goal_id, ref_id, instruction, body, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [("g1", "real-ref", "kept", "kept body", 123),
             ("g1", None, "pre-cutoff", "pre-cutoff body", 124)],
        )
        store._commit()

    gs = GoalState(store)
    apply_legacy_cutoff(store, gs, now_ms=999)

    info = store._db.execute("PRAGMA table_info(goal_deliveries)").fetchall()
    assert next(r for r in info if r["name"] == "ref_id")["notnull"] == 1

    rows = store._db.execute(
        "SELECT ref_id, instruction FROM goal_deliveries WHERE goal_id = 'g1' ORDER BY id"
    ).fetchall()
    # the real ref survives untouched; the NULL one gets a deterministic,
    # namespaced id that cannot collide with a task/program ref
    assert rows[0]["ref_id"] == "real-ref" and rows[0]["instruction"] == "kept"
    assert rows[1]["ref_id"].startswith("pre-cutoff:") and rows[1]["instruction"] == "pre-cutoff"

    with pytest.raises(sqlite3.IntegrityError):
        with store._lock:
            store._db.execute(
                "INSERT INTO goal_deliveries (goal_id, ref_id, instruction, body, created_at) "
                "VALUES ('g1', NULL, 'nope', 'nope', 125)"
            )
    store.close()


def test_the_cutoff_runs_once_and_is_crash_resumable(tmp_path):
    """One migration, one cutoff: the marker is stamped after the sweep, and a
    second call is a no-op that reports nothing migrated."""
    from devclaw.goal.store.legacy_cutoff import CUTOFF_META_KEY, apply_legacy_cutoff

    store = StateStore(str(tmp_path / "devclaw.db"))
    gs = GoalState(store)
    with store._lock:
        store._db.execute(
            "INSERT INTO goal_status (goal_id, lifecycle) VALUES ('g1', 'investigating')"
        )
        store._commit()

    first = apply_legacy_cutoff(store, gs, now_ms=111)
    assert first["lifecycle_healed"] == 1
    assert store.get_meta(CUTOFF_META_KEY) == "111"

    # a pre-shrink value planted AFTER the cutoff is not re-healed — the sweep
    # is one-shot by marker, not by condition
    with store._lock:
        store._db.execute("UPDATE goal_status SET lifecycle = 'firming' WHERE goal_id = 'g1'")
        store._commit()
    assert apply_legacy_cutoff(store, gs, now_ms=222) == {}
    assert store.get_meta(CUTOFF_META_KEY) == "111"
    store.close()


def test_the_cutoff_heals_every_non_executing_lifecycle_including_unknown_ones(tmp_path):
    """The heal is a WHITELIST (``!= 'executing'``), not a list of the values
    we happen to remember. A denylist would miss exactly the case that has no
    reader left — an unrecognised string from a history nobody documented."""
    from devclaw.goal.store.legacy_cutoff import apply_legacy_cutoff

    store = StateStore(str(tmp_path / "devclaw.db"))
    gs = GoalState(store)
    with store._lock:
        store._db.executemany(
            "INSERT INTO goal_status (goal_id, lifecycle) VALUES (?, ?)",
            [("a", None), ("b", "investigating"), ("c", "firming"),
             ("d", "some-forgotten-phase"), ("e", "executing")],
        )
        store._commit()

    assert apply_legacy_cutoff(store, gs, now_ms=1)["lifecycle_healed"] == 4  # not 'e'
    rows = store._db.execute("SELECT lifecycle FROM goal_status").fetchall()
    assert {r["lifecycle"] for r in rows} == {"executing"}
    store.close()


def test_the_cutoff_completes_even_when_drop_column_is_unavailable(tmp_path):
    """``ALTER TABLE ... DROP COLUMN`` needs SQLite 3.35+. On an older engine
    the dead ``inbox_ingest_cursor`` column simply stays — unread and
    unwritten — and everything load-bearing about the sweep still applies.

    The failure this rules out is the one that matters: a cosmetic step
    raising and taking the lifecycle heal, the ref_id backfill and the marker
    down with it, so every boot re-runs a migration that can never finish."""
    import sqlite3

    from devclaw.goal.store.legacy_cutoff import CUTOFF_META_KEY, apply_legacy_cutoff

    class _NoDropColumn:
        """A connection that rejects DROP COLUMN, like SQLite < 3.35."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a, **k):
            if "DROP COLUMN" in sql:
                raise sqlite3.OperationalError('near "DROP": syntax error')
            return self._real.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._real, name)

    store = StateStore(str(tmp_path / "devclaw.db"))
    gs = GoalState(store)
    with store._lock:
        store._db.execute(
            "ALTER TABLE goal_status ADD COLUMN inbox_ingest_cursor INTEGER NOT NULL DEFAULT 0"
        )
        store._db.execute(
            "INSERT INTO goal_status (goal_id, lifecycle) VALUES ('g', 'firming')"
        )
        store._commit()
    store._db = _NoDropColumn(store._db)

    counts = apply_legacy_cutoff(store, gs, now_ms=1)

    assert counts["lifecycle_healed"] == 1                      # the real work landed
    assert store.get_meta(CUTOFF_META_KEY) == "1"               # and it is not re-run
    # the column survives, which is the whole tolerated consequence
    assert any(
        r["name"] == "inbox_ingest_cursor"
        for r in store._db.execute("PRAGMA table_info(goal_status)")
    )
    store.close()


def test_the_cutoff_drops_the_goal_docs_table(tmp_path):
    """``goal_docs`` held checklist / firmed_draft / repo_analysis /
    block_options — every kind died with the host-cognition chain in the spec
    008 shrink. Nothing wrote one after that, nothing read one, and no test
    covered the surface: it survived purely as "pre-shrink rows stay
    readable". The cutoff drops it and says how many rows went."""
    from devclaw.goal.store.legacy_cutoff import apply_legacy_cutoff

    store = StateStore(str(tmp_path / "devclaw.db"))
    gs = GoalState(store)
    with store._lock:
        store._db.execute(
            "CREATE TABLE IF NOT EXISTS goal_docs "
            "(goal_id TEXT, kind TEXT, content TEXT, updated_at INTEGER)"
        )
        store._db.execute(
            "INSERT INTO goal_docs VALUES ('g1', 'checklist', 'dead plan', 1)"
        )
        store._commit()

    assert apply_legacy_cutoff(store, gs, now_ms=1)["goal_docs_dropped"] == 1
    assert store._db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_docs'"
    ).fetchone() is None
    store.close()


def test_duplicate_delivery_ref_id_writes_nothing_new_to_file_or_row(tmp_path):
    """A duplicate ref_id must skip BOTH the row insert and the file mirror —
    never a section with no backing row, and never a row with no section."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    store.append_delivery("g", "ship it", "done", ref_id="only-once")
    before = (tmp_path / "g" / "deliveries.md").read_text()

    store.append_delivery("g", "ship it AGAIN", "done again", ref_id="only-once")

    after = (tmp_path / "g" / "deliveries.md").read_text()
    assert after == before  # nothing appended — the retry was a true no-op
    assert _peek_delivery_row_count(store, "g") == 1
