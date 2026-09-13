"""The devclaw.db schema (spec 046): five tables. ``tasks`` and ``events`` are
the execution record, ``goals`` and ``decisions`` are the goal layer's whole
state, ``meta`` holds the control-plane flags (quota pause, operator hold,
run window, deploy intent). Tables a v1 instance carries beyond these are left
untouched — the v1 database stays readable under its tag.

Migration idiom: ``CREATE TABLE IF NOT EXISTS`` plus idempotent ``ALTER TABLE
… ADD COLUMN`` calls that swallow the already-exists error.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Callable

_TASK_COLUMNS = (
    ("verify_cmd", "TEXT"),
    ("deliver", "INTEGER NOT NULL DEFAULT 0"),
    ("pr_url", "TEXT"),
    ("parent_goal_id", "TEXT"),
    ("pause_count", "INTEGER NOT NULL DEFAULT 0"),
    ("pre_run_sha", "TEXT"),
    ("target_branch", "TEXT"),
    ("project_id", "TEXT"),
    ("exit", "TEXT"),
    ("exit_detail", "TEXT"),
    ("notified", "INTEGER NOT NULL DEFAULT 0"),
)


def bootstrap(db: sqlite3.Connection, lock: threading.RLock, commit: Callable[[], None]) -> None:
    with lock:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id              TEXT PRIMARY KEY,
              kind            TEXT NOT NULL DEFAULT 'implement_feature',
              status          TEXT NOT NULL,
              workspace_dir   TEXT NOT NULL,
              goal            TEXT NOT NULL,
              result_json     TEXT,
              error           TEXT,
              created_at      INTEGER NOT NULL,
              started_at      INTEGER,
              completed_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS events (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id         TEXT NOT NULL,
              type            TEXT NOT NULL,
              source          TEXT NOT NULL DEFAULT '',
              payload_json    TEXT NOT NULL,
              ts              INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
              key             TEXT PRIMARY KEY,
              value           TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
              id              TEXT PRIMARY KEY,
              project_id      TEXT NOT NULL,
              workspace_dir   TEXT NOT NULL,
              repo_url        TEXT NOT NULL DEFAULT '',
              objective       TEXT NOT NULL DEFAULT '',
              issues_json     TEXT NOT NULL DEFAULT '[]',
              branch          TEXT NOT NULL,
              created_at      INTEGER NOT NULL,
              closed_at       INTEGER,
              outcome         TEXT,
              last_seen_json  TEXT,
              last_seen_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS decisions (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              goal_id         TEXT NOT NULL,
              text            TEXT NOT NULL,
              comment_url     TEXT NOT NULL DEFAULT '',
              made_at         INTEGER NOT NULL
            );
            """
        )
        for col, decl in _TASK_COLUMNS:
            try:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # already there
        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created_at  ON tasks(created_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_parent_goal ON tasks(parent_goal_id);
            CREATE INDEX IF NOT EXISTS idx_events_task       ON events(task_id, id);
            CREATE INDEX IF NOT EXISTS idx_events_ts         ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_goals_project     ON goals(project_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_goal    ON decisions(goal_id, id);
            """
        )
        commit()
