"""The project registry — the control plane's source of truth for 'what is
devclaw working on'. CRUD over its own SQLite table + the live status rollup that
joins linked goals on read (never caching their phase)."""
from __future__ import annotations

import pytest

from devclaw.project_registry import (
    ProjectExists,
    ProjectRegistry,
    ResolvedDispatch,
    UnknownProject,
    project_rollup,
)


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(str(tmp_path / "devclaw.db"))


def test_create_get_list(reg):
    reg.create(id="todo", name="Todo App", repo_url="git@x/todo.git")
    reg.create(id="blog", name="Blog")
    assert {p.id for p in reg.list()} == {"todo", "blog"}
    p = reg.get("todo")
    assert p is not None and p.name == "Todo App" and p.repo_url == "git@x/todo.git"
    assert p.status == "active" and p.goal_ids == []


def test_duplicate_id_raises(reg):
    reg.create(id="todo", name="Todo")
    with pytest.raises(ProjectExists):
        reg.create(id="todo", name="Other")


def test_get_unknown_is_none(reg):
    assert reg.get("nope") is None


# ---- spec 003 (#520): registry as the single source of truth for dispatch ----


def test_register_project_validates_and_normalizes_workspace_path(reg):
    """Write-time validation (US3): an absolute container-side path is accepted;
    a relative or empty path is rejected at the write choke point so it can never
    be stored to rot (the 2026-08-12 host-path-for-container-consumer class)."""
    reg.create(id="ok", name="Ok", workspace_dir="/var/lib/devclaw/workspaces/ok")
    assert reg.get("ok").workspace_dir == "/var/lib/devclaw/workspaces/ok"
    with pytest.raises(ValueError):
        reg.create(id="rel", name="Rel", workspace_dir="relative/path")
    with pytest.raises(ValueError):
        reg.create(id="blank", name="Blank", workspace_dir="   ")
    # update path is guarded too
    with pytest.raises(ValueError):
        reg.update("ok", workspace_dir="still/relative")


def test_resolve_dispatch_returns_workspace_and_repo(reg):
    """US1: resolving a registered project_id yields its concrete workspace +
    repo — the facts every dispatch tool needs — read once from the row."""
    reg.create(
        id="fs", name="FS",
        workspace_dir="/var/lib/devclaw/workspaces/fs",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
    )
    resolved = reg.resolve_dispatch("fs")
    assert isinstance(resolved, ResolvedDispatch)
    assert resolved.workspace_dir == "/var/lib/devclaw/workspaces/fs"
    assert resolved.repo_url == "https://github.com/lifekit-hq/finance-sentry.git"


def test_resolve_dispatch_unknown_id_raises_unknown_project(reg):
    """US1: an unknown project_id is a typed miss (KeyError subclass) the tool
    layer turns into a synchronous ToolError — never a silent later failure."""
    with pytest.raises(UnknownProject):
        reg.resolve_dispatch("ghost")
    # UnknownProject is a KeyError so existing `except KeyError` guards catch it
    with pytest.raises(KeyError):
        reg.resolve_dispatch("ghost")


def test_resolve_dispatch_without_workspace_raises(reg):
    """A project registered without a workspace_dir cannot be dispatched to —
    loud ValueError, not a None that fails deep in the engine."""
    reg.create(id="bare", name="Bare", repo_url="git@x/bare.git")
    with pytest.raises(ValueError):
        reg.resolve_dispatch("bare")


def test_update_is_partial_and_bumps_updated_at(reg):
    p0 = reg.create(id="todo", name="Todo")
    p1 = reg.update("todo", preview_url="http://x:8000", status="paused")
    assert p1.preview_url == "http://x:8000"
    assert p1.status == "paused"
    assert p1.name == "Todo"  # untouched
    assert p1.updated_at >= p0.updated_at


def test_update_unknown_raises(reg):
    with pytest.raises(KeyError):
        reg.update("nope", name="x")


def test_link_unlink_idempotent(reg):
    reg.create(id="todo", name="Todo")
    reg.link_goal("todo", "g1")
    reg.link_goal("todo", "g1")  # idempotent
    assert reg.get("todo").goal_ids == ["g1"]
    reg.link_goal("todo", "g2")
    assert reg.get("todo").goal_ids == ["g1", "g2"]
    reg.unlink_goal("todo", "g1")
    assert reg.get("todo").goal_ids == ["g2"]
    reg.unlink_goal("todo", "absent")  # no-op
    assert reg.get("todo").goal_ids == ["g2"]


def test_delete(reg):
    reg.create(id="todo", name="Todo")
    assert reg.delete("todo") is True
    assert reg.get("todo") is None
    assert reg.delete("todo") is False  # already gone


def test_status_filter(reg):
    reg.create(id="a", name="A")
    reg.create(id="b", name="B")
    reg.update("b", status="archived")
    assert {p.id for p in reg.list(status="active")} == {"a"}
    assert {p.id for p in reg.list(status="archived")} == {"b"}


def test_persistence_across_reopen(tmp_path):
    db = str(tmp_path / "devclaw.db")
    ProjectRegistry(db).create(id="todo", name="Todo", goal_ids=["g1"])
    reopened = ProjectRegistry(db)
    p = reopened.get("todo")
    assert p is not None and p.goal_ids == ["g1"]


# ---- rollup + health -------------------------------------------------------
#
# The rollup joins project↔goals by workspace_dir match, NOT by a stored
# goal_ids list (retained as advisory only). Tests below build the input the
# rollup actually gets — a full goals list — and assert the workspace match
# is what drives association.


def _goal(id: str, project_id: str, **fields) -> dict:
    """Build a goals-list entry (goal_service.list_goals shape) for tests. The
    project↔goal join is by project_id (#524 P3), so the 2nd arg is the owning
    project id, not a workspace path."""
    base = {"id": id, "project_id": project_id}
    base.update(fields)
    return base


def test_rollup_joins_by_project_id(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    all_goals = [
        _goal("g1", "todo", phase="in_flight", lifecycle="executing",
              blocked_on=None, progress={"stalled": False},
              direction={"verdict": "on_track"}),
        _goal("g-other", "somewhere-else", phase="in_flight",
              progress={"stalled": False}),
    ]
    out = project_rollup(reg.get("todo"), all_goals)
    assert out["health"] == "working"
    assert len(out["goals"]) == 1
    assert out["goals"][0]["id"] == "g1"
    assert out["goals"][0]["direction"]["verdict"] == "on_track"


def test_rollup_join_survives_a_workspace_rename(reg):
    """#524 P3: the join is by project_id, so renaming the project's
    workspace_dir must NOT drop its goals — the exact fragility the id-key
    replaces (a workspace-path scan would have missed the renamed project)."""
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    reg.update("todo", workspace_dir="/src/todo-RENAMED")
    all_goals = [_goal("g1", "todo", phase="in_flight", progress={"stalled": False})]
    out = project_rollup(reg.get("todo"), all_goals)
    assert len(out["goals"]) == 1 and out["goals"][0]["id"] == "g1"


def test_rollup_ignores_stored_goal_ids(reg):
    """Explicit link_goal calls do NOT bring a goal owned by a different
    project into the rollup. Guards the cancel-and-refile drift the
    project-id match is designed to eliminate."""
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    reg.link_goal("todo", "some-old-goal")  # advisory; must not affect rollup
    all_goals = [
        _goal("some-old-goal", "other", phase="in_flight",
              progress={"stalled": False}),
    ]
    out = project_rollup(reg.get("todo"), all_goals)
    assert out["goals"] == []
    assert out["health"] == "idle"


def test_rollup_matches_by_id_even_without_a_workspace_dir(reg):
    """#524 P3: because the join is by id, a project with no workspace_dir
    still gathers its goals — the old workspace-scan could not."""
    reg.create(id="todo", name="Todo")  # no workspace_dir
    all_goals = [_goal("g1", "todo", phase="in_flight", progress={"stalled": False})]
    out = project_rollup(reg.get("todo"), all_goals)
    assert len(out["goals"]) == 1 and out["health"] == "working"


def test_rollup_health_blocked_on_phase(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    all_goals = [_goal("g1", "todo", phase="blocked",
                       lifecycle="executing", progress={})]
    assert project_rollup(reg.get("todo"), all_goals)["health"] == "blocked"


def test_rollup_health_blocked_on_stall(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    all_goals = [_goal("g1", "todo", phase="idle",
                       lifecycle="executing", progress={"stalled": True})]
    assert project_rollup(reg.get("todo"), all_goals)["health"] == "blocked"


def test_rollup_health_done_when_all_done(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    all_goals = [
        _goal("g1", "todo", phase="done", progress={}),
        _goal("g2", "todo", phase="done", progress={}),
    ]
    assert project_rollup(reg.get("todo"), all_goals)["health"] == "done"


def test_rollup_health_archived_short_circuits(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo")
    reg.update("todo", status="archived")
    all_goals = [_goal("g1", "todo", phase="in_flight", progress={})]
    assert project_rollup(reg.get("todo"), all_goals)["health"] == "archived"


def test_busy_timeout_pragma_applied(reg):
    from devclaw.state_store import SQLITE_BUSY_TIMEOUT_MS

    got = reg._db.execute("PRAGMA busy_timeout").fetchone()[0]
    assert got == SQLITE_BUSY_TIMEOUT_MS
    assert got > 0  # a blocked writer waits, never fails fast at 0


def test_failed_create_does_not_leak_a_write_lock(tmp_path):
    """A duplicate create() raises ProjectExists — but it must ROLL BACK the failed
    INSERT's implicit transaction, not leave it open holding the write lock. The
    open-transaction leak was the root cause of the 75s `database is locked` stall:
    once one connection hit the duplicate-create path, it held the lock until its
    next commit, blocking every other connection's write."""
    db = str(tmp_path / "devclaw.db")
    a = ProjectRegistry(db)
    b = ProjectRegistry(db)
    a.create(id="dup", name="A")  # committed
    with pytest.raises(ProjectExists):
        a.create(id="dup", name="dup-again")  # IntegrityError -> must roll back

    # If `a` leaked the failed INSERT's transaction, it still holds the write lock
    # and this write on a *second* connection blocks until busy_timeout then raises.
    # A short timeout makes the test fail fast (instead of hanging) if the leak is back.
    b._db.execute("PRAGMA busy_timeout = 500")
    b.create(id="other", name="B")  # must succeed promptly — `a` holds no lock
    assert b.get("other") is not None


# ---- automerge: per-project override -----------------------------------
#
# Deliberately NOT a goal.yaml field (see devclaw.goal.merge) — the only place
# auto-merge is configured is here: a project's own override, or nothing
# (meaning "inherit the devclaw-wide default").


def test_automerge_defaults_to_none_on_create(reg):
    p = reg.create(id="todo", name="Todo")
    assert p.automerge is None
    assert reg.get("todo").automerge is None


def test_automerge_set_on_create(reg):
    reg.create(id="on", name="On", automerge=True)
    reg.create(id="off", name="Off", automerge=False)
    assert reg.get("on").automerge is True
    assert reg.get("off").automerge is False


def test_update_omitting_automerge_leaves_it_untouched(reg):
    reg.create(id="todo", name="Todo", automerge=True)
    reg.update("todo", notes="unrelated change")
    assert reg.get("todo").automerge is True


def test_update_can_set_automerge_on_or_off(reg):
    reg.create(id="todo", name="Todo")
    reg.update("todo", automerge=True)
    assert reg.get("todo").automerge is True
    reg.update("todo", automerge=False)
    assert reg.get("todo").automerge is False


def test_update_explicit_none_clears_automerge_override(reg):
    """Passing automerge=None explicitly is different from omitting it — it
    clears a prior pin back to 'inherit the global default'."""
    reg.create(id="todo", name="Todo", automerge=True)
    reg.update("todo", automerge=None)
    assert reg.get("todo").automerge is None


def test_automerge_persists_across_reopen(tmp_path):
    db = str(tmp_path / "devclaw.db")
    ProjectRegistry(db).create(id="todo", name="Todo", automerge=True)
    reopened = ProjectRegistry(db)
    assert reopened.get("todo").automerge is True


def test_automerge_column_migrates_onto_a_pre_existing_table(tmp_path):
    """A projects table created before the automerge column existed must gain
    it on the next open, not error and not lose existing rows."""
    import sqlite3

    db = str(tmp_path / "devclaw.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_url TEXT,
          workspace_dir TEXT, preview_url TEXT, status TEXT NOT NULL DEFAULT 'active',
          goal_ids TEXT, notes TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        """
    )
    con.execute(
        "INSERT INTO projects (id, name, status, created_at, updated_at) "
        "VALUES ('legacy', 'Legacy', 'active', 0, 0)"
    )
    con.commit()
    con.close()

    reg = ProjectRegistry(db)  # must not raise
    p = reg.get("legacy")
    assert p is not None and p.automerge is None  # pre-existing row reads as "inherit"
    reg.update("legacy", automerge=True)  # column is genuinely writable now
    assert reg.get("legacy").automerge is True


def test_find_by_workspace_dir(reg):
    reg.create(id="todo", name="Todo", workspace_dir="/src/todo/", automerge=True)
    found = reg.find_by_workspace_dir("/src//todo")  # normalized match
    assert found is not None and found.id == "todo"
    assert reg.find_by_workspace_dir("/src/nope") is None
    assert reg.find_by_workspace_dir(None) is None
    assert reg.find_by_workspace_dir("") is None


def test_automerge_in_to_dict(reg):
    p = reg.create(id="todo", name="Todo", automerge=False)
    assert p.to_dict()["automerge"] is False


def test_contended_writer_waits_instead_of_failing(tmp_path):
    """Two connections to one db file (the CLI/server split). One holds the write
    lock; the other's write must WAIT for it (busy_timeout) and then succeed —
    not raise `database is locked` as it did before the timeout was set."""
    import threading
    import time

    db = str(tmp_path / "devclaw.db")
    holder = ProjectRegistry(db)
    writer = ProjectRegistry(db)

    holder._db.execute("BEGIN IMMEDIATE")  # grab + hold the single write lock

    released = threading.Event()

    def _release() -> None:
        time.sleep(0.3)  # << the writer's 5s busy_timeout, so it waits then wins
        holder._db.commit()
        released.set()

    t = threading.Thread(target=_release)
    t.start()
    writer.create(id="b", name="B")  # blocks until the holder commits, then writes
    t.join()

    assert released.is_set()
    assert writer.get("b") is not None


# ---- per-project overrides (merge_strategy / autodeploy / review_gate /
#      verify_done) — same three-way + inherit shape as automerge --------------


def test_override_fields_default_to_none_on_create(reg):
    p = reg.create(id="todo", name="Todo")
    assert p.merge_strategy is None and p.autodeploy is None
    assert p.review_gate is None and p.verify_done is None
    got = reg.get("todo")
    assert got.merge_strategy is None and got.autodeploy is None
    assert got.review_gate is None and got.verify_done is None


def test_override_fields_set_on_create_and_persist(tmp_path):
    db = str(tmp_path / "devclaw.db")
    ProjectRegistry(db).create(
        id="p", name="P", workspace_dir="/src/p",
        merge_strategy="rebase", autodeploy=False, review_gate=True, verify_done=False,
    )
    got = ProjectRegistry(db).get("p")  # reopen — proves durable
    assert got.merge_strategy == "rebase"
    assert got.autodeploy is False
    assert got.review_gate is True
    assert got.verify_done is False


def test_update_three_way_semantics_per_override_field(reg):
    reg.create(id="p", name="P", autodeploy=True, merge_strategy="squash")
    # omit → untouched
    reg.update("p", notes="unrelated")
    assert reg.get("p").autodeploy is True and reg.get("p").merge_strategy == "squash"
    # concrete → pinned
    reg.update("p", autodeploy=False, merge_strategy="merge")
    assert reg.get("p").autodeploy is False and reg.get("p").merge_strategy == "merge"
    # explicit None → cleared back to inherit
    reg.update("p", autodeploy=None, merge_strategy=None)
    assert reg.get("p").autodeploy is None and reg.get("p").merge_strategy is None


def test_override_fields_in_to_dict(reg):
    p = reg.create(id="p", name="P", review_gate=False, verify_done=True, merge_strategy="rebase")
    d = p.to_dict()
    assert d["reviewGate"] is False and d["verifyDone"] is True
    assert d["mergeStrategy"] == "rebase" and d["autodeploy"] is None


def test_all_override_columns_migrate_onto_a_legacy_table(tmp_path):
    """A projects table created before ANY override column existed must gain
    all of them on next open — not error, not drop rows."""
    import sqlite3

    db = str(tmp_path / "devclaw.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_url TEXT,
          workspace_dir TEXT, preview_url TEXT, status TEXT NOT NULL DEFAULT 'active',
          goal_ids TEXT, notes TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        """
    )
    con.execute(
        "INSERT INTO projects (id, name, status, created_at, updated_at) "
        "VALUES ('legacy', 'Legacy', 'active', 0, 0)"
    )
    con.commit()
    con.close()

    reg = ProjectRegistry(db)  # must not raise; must add all override columns
    p = reg.get("legacy")
    assert p is not None
    assert p.automerge is None and p.merge_strategy is None
    assert p.autodeploy is None and p.review_gate is None and p.verify_done is None
    reg.update("legacy", merge_strategy="rebase", review_gate=True)  # columns writable
    assert reg.get("legacy").merge_strategy == "rebase"
    assert reg.get("legacy").review_gate is True


# ---- resolve_override: the generic per-project resolution seam ---------------


def test_resolve_override_project_value_wins_over_default(reg):
    reg.create(id="p", name="P", workspace_dir="/src/p", autodeploy=False)
    # default says True (env default), project pins False → project wins.
    assert reg.resolve_override("p", "autodeploy", True) is False
    # a field the project didn't pin falls back to the default.
    assert reg.resolve_override("p", "verify_done", True) is True


def test_explicit_autodeploy_pin_resolves_over_conditional_none_default(reg):
    """#554: the fleet autodeploy default is now ``None`` (= conditional,
    resolved by app-surface detection at the done-gate). An explicit project
    pin must resolve THROUGH that None default unchanged — and a project with
    no pin (registered before or after the change) resolves to None, i.e. to
    the conditional path, never to a hard on/off."""
    reg.create(id="pinned", name="P", workspace_dir="/src/p", autodeploy=True)
    reg.create(id="unpinned", name="U", workspace_dir="/src/u")
    assert reg.resolve_override("pinned", "autodeploy", None) is True
    assert reg.resolve_override("unpinned", "autodeploy", None) is None


def test_resolve_override_unregistered_workspace_returns_default(reg):
    assert reg.resolve_override("/src/nope", "review_gate", True) is True
    assert reg.resolve_override(None, "review_gate", False) is False


def test_resolve_override_string_field(reg):
    reg.create(id="p", name="P", workspace_dir="/src/p", merge_strategy="rebase")
    assert reg.resolve_override("p", "merge_strategy", "squash") == "rebase"
    reg.create(id="q", name="Q", workspace_dir="/src/q")  # no pin
    assert reg.resolve_override("q", "merge_strategy", "squash") == "squash"


def test_resolve_override_rejects_unknown_field(reg):
    with pytest.raises(ValueError):
        reg.resolve_override("p", "not_a_field", True)


def test_resolve_override_survives_a_workspace_rename(reg):
    """#524 P3: knobs resolve by project_id, so renaming the project's
    workspace_dir does NOT unbind them — the exact fragility the id-key
    eliminates (the old workspace scan would have missed the renamed row)."""
    reg.create(id="p", name="P", workspace_dir="/src/p", automerge=True)
    reg.update("p", workspace_dir="/src/p-RENAMED")
    assert reg.resolve_override("p", "automerge", None) is True


def test_resolve_override_none_project_id_returns_default(reg):
    """A goal/task with no owning project (self-fix, legacy pre-P3 row) falls to
    the default — never raises, never scans."""
    assert reg.resolve_override(None, "automerge", "DFLT") == "DFLT"


def test_backfill_stamps_project_id_from_legacy_workspace(tmp_path):
    """#524 P3 migration: a goal written before the field is stamped with its
    owning project's id (resolved by the legacy workspace match) so its knobs
    keep resolving across the cutover. Idempotent."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue
    from tests.goal_fakes import seed_goal

    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "t.db"))
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="proj", name="P", workspace_dir="/repos/demo", automerge=True)
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900,
                     verify_done=False)
    svc = GoalService(TaskQueue(db), db, config=cfg, project_registry=reg)
    seed_goal(goals_dir, "legacy-g")  # workspace_dir=/repos/demo, NO project_id
    assert svc._goal_store.load_goal("legacy-g").project_id is None

    assert svc.backfill_project_ids() == 1
    assert svc._goal_store.load_goal("legacy-g").project_id == "proj"
    # idempotent — a second run stamps nothing (already set)
    assert svc.backfill_project_ids() == 0
    db.close()


# ---- managed-repo provenance ------------------------------------------------


def test_managed_repo_ledger_records_case_insensitively_and_survives_reopen(tmp_path):
    """The delete_repo ownership gate reads this ledger: slugs match however
    GitHub cases them, and provenance must outlive a server restart."""
    db = str(tmp_path / "devclaw.db")
    reg = ProjectRegistry(db)

    assert not reg.is_managed_repo("dsdevq/scratch")
    reg.record_managed_repo("dsdevq/Scratch")
    assert reg.is_managed_repo("dsdevq/scratch")
    assert reg.is_managed_repo("DSDEVQ/SCRATCH")
    reg.record_managed_repo("dsdevq/Scratch")  # idempotent, no raise

    reopened = ProjectRegistry(db)
    assert reopened.is_managed_repo("dsdevq/scratch")

    reopened.forget_managed_repo("Dsdevq/Scratch")
    assert not reopened.is_managed_repo("dsdevq/scratch")
    reopened.forget_managed_repo("dsdevq/scratch")  # forgetting twice is a no-op


# ---- shared-file bootstrap: the migration must tolerate a concurrent writer ----


def test_add_column_is_idempotent_when_another_writer_won_the_race(tmp_path):
    """The registry db is SHARED — the CLI and the server each open their own
    connection (see the class docstring) — so two processes can bootstrap it at
    once. The migration used to read `PRAGMA table_info` and then ALTER only the
    columns it saw missing, which is a TOCTOU race: if the other writer adds the
    column in that window, the ALTER raises `duplicate column name` and the
    loser crashes on startup. `_add_column` treats "already there" as the
    success case, the way StateStore._bootstrap and GoalStore's migrator always
    have — this was the one migrator that didn't.

    Surfaced by running the suite with `-n auto`: sixteen workers importing
    `devclaw.server.tools` bootstrapped one repo-root devclaw.db simultaneously.
    """
    reg = ProjectRegistry(str(tmp_path / "shared.db"))

    # Bootstrap already added them, so these are exactly the statements the
    # losing writer issues after its stale introspection. None may raise.
    reg._add_column("sandbox_image", "TEXT")
    reg._add_column("automerge", "INTEGER")

    # ...and the table is intact afterwards.
    reg.create(id="p", name="P", automerge=True)
    assert reg.get("p").automerge is True


def test_bootstrap_completes_on_a_partially_migrated_table(tmp_path):
    """A db left half-migrated (one override column added, the rest not) must
    finish migrating on the next open rather than stopping at the first column
    that already exists."""
    import sqlite3

    db = str(tmp_path / "shared.db")
    seed = sqlite3.connect(db)
    # The pre-override table shape: every base column, none of the per-project
    # override columns the migration adds.
    seed.executescript(
        """
        CREATE TABLE projects (
          id            TEXT PRIMARY KEY,
          name          TEXT NOT NULL,
          repo_url      TEXT,
          workspace_dir TEXT,
          preview_url   TEXT,
          status        TEXT NOT NULL DEFAULT 'active',
          goal_ids      TEXT,
          notes         TEXT,
          created_at    INTEGER NOT NULL,
          updated_at    INTEGER NOT NULL
        );
        """
    )
    seed.execute("ALTER TABLE projects ADD COLUMN sandbox_image TEXT")
    seed.commit()
    seed.close()

    reg = ProjectRegistry(db)

    cols = {row[1] for row in reg._db.execute("PRAGMA table_info(projects)")}
    for name in ("sandbox_image", "automerge", "autodeploy", "review_gate",
                 "verify_done", "merge_strategy", "browser_gate_mode"):
        assert name in cols, name

    reg.create(id="p", name="P")
    assert reg.get("p").name == "P"


def test_suite_never_writes_its_database_into_the_repo():
    """`devclaw.server._state` opens a real StateStore/ProjectRegistry at IMPORT
    time against DEVCLAW_DB, which defaults to `devclaw.db` relative to CWD.
    Importing a server module from a test therefore used to create a live
    database in the repo root — one file shared by every xdist worker. conftest
    points it at a per-worker temp path; this pins that it stays outside the
    checkout.
    """
    import os
    from pathlib import Path

    db = Path(os.environ["DEVCLAW_DB"]).resolve()
    repo = Path(__file__).resolve().parents[1]
    assert repo not in db.parents, f"suite db {db} is inside the checkout {repo}"
