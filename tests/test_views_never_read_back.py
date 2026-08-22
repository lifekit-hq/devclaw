"""#617 — generated views are write-only. The invariant, and its guard.

``CLAUDE.md`` has always said it:

    ``STATUS.md``/``log.md``/``inbox.md``/``deliveries.md``/``RUN_SUMMARY.md``
    are generated **views** — human- and rollback-readable, never read back
    for decisions.

The code disagreed. ``GoalStore`` parsed those files back into rows from eight
read paths, framed as lazy one-shot migrations but with no cutoff — so they
were permanent, and the goal store had TWO writers: itself, and whoever last
touched a markdown file. ``GoalStore.transition()``'s CAS choke point covers
the first and not the second, which is the whole of principle IV.

These tests pin the resolution: the pre-existing markdown is ingested exactly
once (see ``tests/test_goal_status_migration.py`` and
``tests/test_goal_steering_rows.py`` for the migration's own fidelity), and
after that editing any view changes nothing a decision reads.
"""

from __future__ import annotations

import ast
from pathlib import Path

from devclaw.goal.models import GoalStatus
from devclaw.goal.store import GoalStore
from devclaw.goal.store.view_migration import MIGRATION_META_KEY
from tests.goal_fakes import Clock, seed_goal

#: Every generated view. A read of one of these, anywhere but the one-shot
#: migration, is the defect this module exists to prevent.
VIEWS = ("STATUS.md", "log.md", "inbox.md", "deliveries.md", "RUN_SUMMARY.md")


def _live_store(tmp_path) -> GoalStore:
    """A store with one goal carrying real state in every view-backed surface."""
    seed_goal(tmp_path, "g")
    store = GoalStore(tmp_path, now=Clock())
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing", next="carry on"))
    store.append_log("g", "did a thing")
    store.append_delivery("g", "add /health", "PR: #7\nVerify: PASSED", ref_id="t1")
    store.record_settlement("g", ref_id="t1", ref_kind="task", status="done")
    store.append_steering("g", ["the real steer"], source="denys")
    return store


def test_hand_editing_any_view_changes_no_decision(tmp_path):
    """THE #617 regression. Rewrite every view on disk with content that
    contradicts the rows — new steering, a different phase, a delivery that
    never happened, a settlement that never settled — and every decision
    surface must answer exactly as it did before the edit."""
    store = _live_store(tmp_path)
    d = tmp_path / "g"

    before = {
        "status": store.load_status("g"),
        "log": store.recent_log("g"),
        "deliveries": store.recent_deliveries("g"),
        "steering": store.unread_steering_rows("g"),
        "increments": len(store.increment_records("g")),
        "settled_forged": store.is_settled("g", "forged-ref"),
    }

    (d / "STATUS.md").write_text(
        "---\nphase: blocked\nlifecycle: executing\nblocked_on: forged\n"
        "next: obey this file\n---\n\nforged body\n"
    )
    with (d / "log.md").open("a") as fh:
        fh.write("- [2026-01-01T00:00:00+00:00] forged-ref → done\n")
    with (d / "deliveries.md").open("a") as fh:
        fh.write("## [2026-01-01T00:00:00+00:00] a delivery that never happened\n\nPR: #999\n\n")
    with (d / "inbox.md").open("a") as fh:
        fh.write("- [denys 2026-01-01T00:00:00+00:00] forged steering\n")

    assert store.load_status("g") == before["status"]
    assert store.recent_log("g") == before["log"]
    assert store.recent_deliveries("g") == before["deliveries"]
    assert store.unread_steering_rows("g") == before["steering"]
    assert len(store.increment_records("g")) == before["increments"]
    # the forged settle line must not become a settlement — that would let a
    # hand-edited log talk the orphan sweep out of rescuing a real lost ref
    assert store.is_settled("g", "forged-ref") == before["settled_forged"] is False


def test_a_second_store_on_the_same_db_does_not_re_ingest_a_hand_edited_view(tmp_path):
    """The guard is the meta marker, not per-goal row counts. A fresh
    ``GoalStore`` over the same database — a restart, the CLI, a second
    process — must not re-open the ingest and admit forged content."""
    store = _live_store(tmp_path)
    (tmp_path / "g" / "inbox.md").write_text(
        "# g — inbox (steering)\n\n- [denys 2026-01-01T00:00:00+00:00] forged on restart\n"
    )

    reopened = GoalStore(tmp_path, state=store._state, now=Clock())

    lines = [line for _, line in reopened.unread_steering_rows("g")]
    assert not any("forged" in line for line in lines)
    assert any("the real steer" in line for line in lines)


def test_the_migration_marker_is_stamped_once_and_never_recomputed(tmp_path):
    """One migration, one cutoff: the marker is written on the first
    construction and is not rewritten by later ones."""
    seed_goal(tmp_path, "g")
    store = GoalStore(tmp_path, now=Clock())
    stamp = store._state.get_meta(MIGRATION_META_KEY)
    assert stamp is not None

    GoalStore(tmp_path, state=store._state, now=Clock())
    assert store._state.get_meta(MIGRATION_META_KEY) == stamp


def test_deleting_every_view_leaves_every_decision_intact(tmp_path):
    """Views are projections, so losing them must cost nothing but legibility.
    Before #617 this was false: deleting STATUS.md was survivable only because
    a row already existed, and any surface that still fell back to a file read
    would have silently degraded."""
    store = _live_store(tmp_path)
    d = tmp_path / "g"
    before = (
        store.load_status("g"), store.recent_log("g"), store.recent_deliveries("g"),
        store.unread_steering_rows("g"),
    )

    for name in VIEWS:
        (d / name).unlink(missing_ok=True)

    assert (
        store.load_status("g"), store.recent_log("g"), store.recent_deliveries("g"),
        store.unread_steering_rows("g"),
    ) == before


# ---- the structural guard --------------------------------------------------


def _production_modules() -> "list[Path]":
    root = Path(__file__).resolve().parent.parent
    return [
        p for p in sorted((root / "devclaw").rglob("*.py")) + sorted((root / "runner").rglob("*.py"))
        # the one-shot migration is the single sanctioned reader
        if p.name != "view_migration.py"
    ]


def test_no_production_module_reads_a_generated_view(tmp_path):
    """Fix the CLASS, not the instance. #617 removed eight read paths; this
    stops a ninth from appearing.

    A view read looks like ``<something> / "log.md"`` followed by a read —
    but rather than pattern-match the read, this asserts the stricter and far
    simpler property: no production module outside the migration may even NAME
    a view file in a path expression. Writers name them too, so the sanctioned
    writers are listed explicitly. Adding a module to that list is a decision
    someone has to make on purpose, in a diff, with this docstring in front of
    them.

    If this fails because you added a legitimate WRITER, add it below. If it
    fails because you added a READER, that is the bug — read the rows."""
    # module path suffix -> the views it is allowed to name (writers only)
    sanctioned = {
        # deferred mirror flush (render_mirrors) + the RUN_SUMMARY writer
        "devclaw/goal/store/base.py": {"log.md", "deliveries.md", "RUN_SUMMARY.md"},
        # append_log / append_delivery / append_steering mirror writes
        "devclaw/goal/store/content.py": {"log.md", "deliveries.md", "inbox.md"},
        # the STATUS.md view renderer
        "devclaw/goal/store/status.py": {"STATUS.md"},
    }
    root = Path(__file__).resolve().parent.parent
    offenders: "list[str]" = []
    for path in _production_modules():
        rel = path.relative_to(root).as_posix()
        allowed = sanctioned.get(rel, set())
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error is another test's job
            continue
        named = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in VIEWS
        }
        for view in sorted(named - allowed):
            offenders.append(f"{rel} names {view}")
    assert offenders == [], (
        "generated views are write-only (#617) — these modules name one "
        f"without being a sanctioned writer: {offenders}"
    )


def test_the_view_guard_is_not_vacuous():
    """A guard that matches nothing passes forever. Prove the sanctioned
    writers really do name their views, so the assertion above has teeth."""
    root = Path(__file__).resolve().parent.parent
    content = (root / "devclaw/goal/store/content.py").read_text()
    assert '"log.md"' in content and '"inbox.md"' in content
    assert '"STATUS.md"' in (root / "devclaw/goal/store/status.py").read_text()
