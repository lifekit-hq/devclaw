"""Tranche 1 substrate — the SQLite home for goal state.

Goal state used to be spread across per-goal files under ``DEVCLAW_GOALS_DIR``
(``goal.yaml`` / ``STATUS.md`` / ``log.md`` / ``inbox.md`` / ``deliveries.md`` …)
and linked to the task queue only by a string goal id. The approved Tranche 1
plan consolidates that state into the SAME ``devclaw.db`` SQLite database that
:class:`devclaw.state_store.StateStore` already owns (WAL, one shared
connection guarded by a single ``threading.RLock``), so a task row and its
owning goal's state can be written in one atomic :meth:`StateStore.transaction`.

**PR3 brought ``goal_status`` + ``goal_phase_history`` LIVE.** ``GoalState``
now owns the status read/write surface (:meth:`read_status` /
:meth:`write_status` / the phase-history methods); ``GoalStore.load_status`` /
``save_status`` are re-backed onto it, with ``STATUS.md`` demoted to a
generated full-fidelity view (the rollback path).

**PR5 brought ``goal_steering`` LIVE.** ``consumed_at IS NULL`` is now the
source of truth for "unread" — ``GoalStore.append_steering`` writes rows,
``GoalStore.transition(consume_steering=...)`` consumes them by exact id.
``inbox.md`` is a generated mirror only: since #617 nothing reads it back, and
steering enters exclusively through ``steer_goal``.

**PR6 brought ``goal_log`` / ``goal_deliveries`` LIVE.**
``goal_log`` and ``goal_deliveries`` are row-backed with ``log.md`` /
``deliveries.md`` as generated mirrors; ``goal_deliveries`` inserts are
idempotent, keyed on ``ref_id`` (``UNIQUE(goal_id, ref_id)`` + INSERT OR
IGNORE), closing a PR4-review nuance where a settle landing in a
``TransitionConflict`` retry window could append the same delivery twice.
The views are write-only projections (#617) — never read back.

**The #616 cutoff (2026-08-22) left exactly one shape per row.** ``lifecycle``
is non-optional, ``ref_id`` is NOT NULL, and ``goal_docs`` — whose every kind
died with the host-cognition chain in the spec 008 shrink — is dropped. (The
one-shot cutoff/ingest modules themselves were deleted by the 2026-08-29
prune, having run on the one production DB.) The compat branches that used to
read those shapes are deleted, not disabled.

The class was split into status/content mixins for legibility
(behavior-preserving), mirroring the exact seam its wrapper ``GoalStore``
already has (:mod:`devclaw.goal.store.status` / :mod:`devclaw.goal.store
.content`):

- :mod:`.state_status` — :class:`~devclaw.goal.state_status
  .GoalStateStatusMixin`: the ``goal_status`` row surface + the append-only
  ``goal_phase_history``.
- :mod:`.state_content` — :class:`~devclaw.goal.state_content
  .GoalStateContentMixin`: ``goal_steering`` / ``goal_log`` /
  ``goal_deliveries`` / ``project_docs`` / ``goal_settlements``.
- this module — the class head, the table bootstrap, and the composed
  :class:`GoalState` every importer keeps using.

The mixins run on the same ``self._store`` instance, so the shared-connection /
``RLock`` / ``transaction()`` semantics are byte-identical to the pre-split
monolith.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .state_content import GoalStateContentMixin
from .state_problems import GoalStateProblemsMixin
from .state_status import GoalStateStatusMixin

if TYPE_CHECKING:
    from ..state_store import StateStore


class GoalState(GoalStateStatusMixin, GoalStateContentMixin, GoalStateProblemsMixin):
    """Owns the goal-state tables inside a shared :class:`StateStore`.

    Handed a ``StateStore``, it borrows that store's single sqlite connection,
    its ``RLock``, and its ``transaction()`` seam — so goal-state writes land in
    the same database and can join the same atomic unit as task/program writes.
    Construction bootstraps the tables idempotently (``CREATE TABLE IF NOT
    EXISTS`` + forward-compat ALTERs), mirroring the store's own ``_bootstrap``
    style. Since PR3 it also carries the status read/write surface
    (``goal_status`` + ``goal_phase_history``); the other tables stay unused
    until later Tranche 1 PRs migrate onto them.
    """

    def __init__(self, store: "StateStore") -> None:
        self._store = store
        self._bootstrap()

    def _bootstrap(self) -> None:
        # Idempotent — safe to run on every construction (matches
        # StateStore._bootstrap). Uses the shared connection + lock; commits via
        # the store's _commit(), a no-op inside an open transaction().
        with self._store._lock:
            self._store._db.executescript(
                """
                -- One row per goal: the machine state STATUS.md held in YAML
                -- frontmatter before Tranche 1/PR3. This table is now the
                -- source of truth for status; STATUS.md is a generated
                -- full-fidelity view written on every save (the rollback path).
                -- `phase`/`lifecycle` are the current GoalStatus fields;
                -- `state` (PR4) holds the consolidated devclaw.goal.transitions
                -- .State value, stamped by GoalStore on every write (nullable
                -- only for a pre-PR4 row that hasn't been re-saved yet).
                -- `version` (PR4) is the optimistic-concurrency counter
                -- GoalStore.transition() CAS's against, bumped by exactly 1 on
                -- every write; in_flight_* carry the durable pointer to the
                -- goal's running task/program (in_flight_json is the
                -- authoritative serialized InFlight, ref_id/kind denormalized
                -- for later indexing).
                CREATE TABLE IF NOT EXISTS goal_status (
                  goal_id               TEXT PRIMARY KEY,
                  version               INTEGER NOT NULL DEFAULT 0,
                  state                 TEXT,
                  phase                 TEXT,
                  lifecycle             TEXT,
                  blocked_on            TEXT,
                  blocked_kind          TEXT,
                  heal_attempts         INTEGER NOT NULL DEFAULT 0,
                  next_heal_at          TEXT,
                  envcap_redispatches   INTEGER NOT NULL DEFAULT 0,
                  pending_merge_pr      TEXT NOT NULL DEFAULT '',
                  merge_heal_attempted  INTEGER NOT NULL DEFAULT 0,
                  next                  TEXT,
                  last_plan_at          TEXT,
                  last_tick_at          TEXT,
                  actions_dispatched    INTEGER,
                  donegate_rounds       INTEGER NOT NULL DEFAULT 0,
                  donegate_progress     INTEGER NOT NULL DEFAULT 0,
                  problem_id            TEXT NOT NULL DEFAULT '',
                  slice_hold_count      INTEGER NOT NULL DEFAULT 0,
                  last_eval_verdict     TEXT,
                  last_eval_at          TEXT,
                  last_eval_note        TEXT,
                  last_progress_at      TEXT,
                  no_progress_notified  INTEGER,
                  in_flight_ref_id      TEXT,
                  in_flight_kind        TEXT,
                  in_flight_json        TEXT,
                  updated_at            INTEGER
                );

                -- Append-only phase transitions (STATUS.md phase_history today).
                CREATE TABLE IF NOT EXISTS goal_phase_history (
                  id         INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id    TEXT NOT NULL,
                  phase      TEXT NOT NULL,
                  at         TEXT NOT NULL
                );

                -- Convergence ledger (spec 018 US1): ONE row per terminal
                -- goal event, written at the close/cancel transition —
                -- eval_outcomes' settle-ledger pattern applied to goals.
                -- rounds = done-gate proposals over the goal's LIFE (counted
                -- from goal_phase_history 'verifying' entries at write time,
                -- so a mid-goal steer's donegate_rounds reset can't hide
                -- churn). The scorecard reads this; nothing else does.
                CREATE TABLE IF NOT EXISTS goal_convergence (
                  goal_id        TEXT PRIMARY KEY,
                  outcome        TEXT NOT NULL,   -- 'achieved' | 'abandoned'
                  rounds         INTEGER NOT NULL,
                  workspace_dir  TEXT,
                  closed_at      TEXT NOT NULL
                );

                -- Steering lines (inbox.md is a generated mirror; since
                -- #617 nothing reads it back). consumed_at NULL == unread, the
                -- source of truth for what the planner hasn't seen yet;
                -- GoalStore.transition(consume_steering=[...]) stamps it,
                -- atomically with the decision the steering informed.
                CREATE TABLE IF NOT EXISTS goal_steering (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id     TEXT NOT NULL,
                  source      TEXT NOT NULL,
                  line        TEXT NOT NULL,
                  created_at  INTEGER NOT NULL,
                  consumed_at INTEGER
                );

                -- Append-only event log (log.md today).
                CREATE TABLE IF NOT EXISTS goal_log (
                  id       INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id  TEXT NOT NULL,
                  ts       INTEGER NOT NULL,
                  message  TEXT NOT NULL
                );

                -- Grounded record of what each action shipped (deliveries.md
                -- today, PR6). ref_id is the settle's dispatched-ref id and is
                -- REQUIRED (#616 cutoff): it is what makes the INSERT
                -- idempotent under UNIQUE(goal_id, ref_id), and while it could
                -- be NULL that dedupe silently did not apply — SQLite treats
                -- every NULL as distinct. Rows ingested from a pre-cutoff
                -- deliveries.md carry a deterministic 'pre-cutoff:<n>' id.
                CREATE TABLE IF NOT EXISTS goal_deliveries (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id     TEXT NOT NULL,
                  ref_id      TEXT NOT NULL,
                  instruction TEXT,
                  body        TEXT,
                  created_at  INTEGER NOT NULL,
                  UNIQUE(goal_id, ref_id)
                );

                -- One row per settled in-flight ref — the dedupe key a later
                -- settle/reconcile pass uses to tell "settled and recorded"
                -- from "the in_flight ref was lost before the result was seen".
                CREATE TABLE IF NOT EXISTS goal_settlements (
                  goal_id    TEXT NOT NULL,
                  ref_id     TEXT NOT NULL,
                  ref_kind   TEXT,
                  status     TEXT,
                  settled_at INTEGER,
                  UNIQUE(goal_id, ref_id)
                );

                -- PROJECT-scoped documents (mission-control borrow item 3):
                -- A goal's own state dies with it, so every new goal on the
                -- same repo relearned build quirks from zero. scope_key is the
                -- NORMALIZED workspace_dir (project_registry._normalize_
                -- workspace — the same join key the registry uses), NOT a
                -- goal/project id: the brief must survive goal cancel+refile
                -- and project re-registration alike. Host-side on purpose —
                -- the sandbox workspace is `git clean -fdx`-wiped per
                -- dispatch, so nothing left in-repo survives between tasks.
                CREATE TABLE IF NOT EXISTS project_docs (
                  scope_key  TEXT NOT NULL,
                  kind       TEXT NOT NULL,
                  content    TEXT,
                  updated_at INTEGER,
                  PRIMARY KEY(scope_key, kind)
                );

                -- Issue-keyed identity for one_shot companion goals (spec 022 US1).
                -- PRIMARY KEY on (project_id, issue_key) implies NOT NULL on both
                -- columns — spec 012's lesson: a nullable ref silently disables its
                -- own constraint. Racing creation attempts hit an IntegrityError;
                -- only one caller wins the row (and thus the goal creation).
                CREATE TABLE IF NOT EXISTS goal_issue_identity (
                  project_id  TEXT NOT NULL,
                  issue_key   TEXT NOT NULL,
                  goal_id     TEXT NOT NULL,
                  created_at  INTEGER NOT NULL,
                  PRIMARY KEY (project_id, issue_key)
                );
                -- spec 031: typed Problems and the owner's Decisions. Append-only;
                -- "current" is a query. Written only inside the BLOCK/UNBLOCK txn.
                CREATE TABLE IF NOT EXISTS goal_problems (
                  id                 TEXT PRIMARY KEY,
                  goal_id            TEXT NOT NULL,
                  kind               TEXT NOT NULL,
                  raised_by          TEXT NOT NULL,
                  what               TEXT NOT NULL,
                  clause             TEXT NOT NULL DEFAULT '',
                  why                TEXT NOT NULL DEFAULT '',
                  options_json       TEXT NOT NULL,
                  default_key        TEXT NOT NULL,
                  timebox_at         INTEGER NOT NULL,
                  status             TEXT NOT NULL DEFAULT 'open',
                  raised_at          INTEGER NOT NULL,
                  closed_at          INTEGER,
                  closed_by_decision TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_goal_problems_goal_status
                  ON goal_problems(goal_id, status);
                CREATE TABLE IF NOT EXISTS goal_decisions (
                  id            TEXT PRIMARY KEY,
                  goal_id       TEXT NOT NULL,
                  problem_id    TEXT NOT NULL DEFAULT '',
                  clause        TEXT NOT NULL DEFAULT '',
                  verb          TEXT NOT NULL,
                  option_key    TEXT NOT NULL DEFAULT '',
                  text          TEXT NOT NULL DEFAULT '',
                  provenance    TEXT NOT NULL,
                  made_by       TEXT NOT NULL DEFAULT '',
                  made_at       INTEGER NOT NULL,
                  superseded_by TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_goal_decisions_goal_clause
                  ON goal_decisions(goal_id, clause);

                -- goal_id lookups on the append-only / multi-row tables.
                CREATE INDEX IF NOT EXISTS idx_goal_phase_history_goal
                  ON goal_phase_history(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_steering_goal
                  ON goal_steering(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_log_goal
                  ON goal_log(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_deliveries_goal
                  ON goal_deliveries(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_settlements_goal
                  ON goal_settlements(goal_id, ref_id);
                """
            )

            # Forward-compat ALTERs for DBs bootstrapped by PR2 (which created
            # goal_status WITHOUT phase/lifecycle — those columns were only added
            # in PR3, when the table went live) and for pre-blocked_kind DBs
            # (the column landed with the F8-prerequisite block classification).
            # Idempotent: a fresh DB already has them from the CREATE above, so
            # the duplicate-column error is swallowed. Mirrors
            # StateStore._bootstrap's ALTER pattern.
            for sql in (
                "ALTER TABLE goal_status ADD COLUMN phase TEXT",
                "ALTER TABLE goal_status ADD COLUMN lifecycle TEXT",
                "ALTER TABLE goal_status ADD COLUMN blocked_kind TEXT",
                "ALTER TABLE goal_status ADD COLUMN heal_attempts INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN next_heal_at TEXT",
                "ALTER TABLE goal_status ADD COLUMN donegate_rounds INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN donegate_progress INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN problem_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE goal_status ADD COLUMN envcap_redispatches INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN slice_hold_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN pending_merge_pr TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE goal_status ADD COLUMN merge_heal_attempted INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    self._store._db.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists

            self._store._commit()
