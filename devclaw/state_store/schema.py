"""The devclaw.db schema — every CREATE TABLE / lazy ALTER, in one module.

Pure DDL bootstrap, split out of :mod:`devclaw.state_store.core` so the store
module reads as behavior and this one as schema. Called exactly once, from
``StateStore.__init__`` via ``StateStore._bootstrap`` — same lock, same
connection, same commit discipline; the body is the pre-split method verbatim.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Callable


def bootstrap(db: sqlite3.Connection, lock: threading.RLock, commit: Callable[[], None]) -> None:
        with lock:
            # (1) Create tables (idempotent). CREATE TABLE for `tasks` is the
            # current schema; pre-existing DBs get caught up by the ALTERs below.
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id              TEXT PRIMARY KEY,
                  kind            TEXT NOT NULL DEFAULT 'implement_feature',
                  status          TEXT NOT NULL,
                  workspace_dir   TEXT NOT NULL,
                  goal            TEXT NOT NULL,
                  notify_url      TEXT,
                  result_json     TEXT,
                  error           TEXT,
                  created_at      INTEGER NOT NULL,
                  started_at      INTEGER,
                  completed_at    INTEGER,
                  milestone       TEXT,
                  verify_cmd      TEXT,
                  deliver         INTEGER NOT NULL DEFAULT 0,
                  pr_url          TEXT,
                  title           TEXT,
                  parent_goal_id  TEXT,
                  pause_count     INTEGER NOT NULL DEFAULT 0,
                  scaffold        INTEGER NOT NULL DEFAULT 0
                );

                -- Raw runner SDK events (one row per agent action inside every
                -- task) — the highest-volume append-only log after traces. Rows
                -- are never mutated (append + a daily retention DELETE of rows
                -- older than DEVCLAW_EVENTS_RETENTION_DAYS — see
                -- maybe_prune_events). Read by get_events + the SSE layer, which
                -- uses the monotonic id as its resume cursor.
                CREATE TABLE IF NOT EXISTS events (
                  id              INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id         TEXT NOT NULL,
                  type            TEXT NOT NULL,
                  source          TEXT NOT NULL DEFAULT '',
                  payload_json    TEXT NOT NULL,
                  ts              INTEGER NOT NULL
                );

                -- small key/value for process-wide flags (e.g. the global quota
                -- pause). Survives restart so a pause isn't lost on a recreate.
                CREATE TABLE IF NOT EXISTS meta (
                  key             TEXT PRIMARY KEY,
                  value           TEXT NOT NULL
                );

                -- Per-goal-tick trace events: every cognition call, dispatch,
                -- delivery, subprocess, notify, etc. that a heartbeat tick
                -- emitted. Grouped by trace_id (one per tick) so the full
                -- causal chain of a tick can be replayed. Rows are never
                -- mutated (append + a daily retention DELETE of rows older
                -- than DEVCLAW_TRACE_RETENTION_DAYS — see maybe_prune_traces).
                -- Read by the get_trace MCP tool and the dashboard.
                CREATE TABLE IF NOT EXISTS traces (
                  id              INTEGER PRIMARY KEY AUTOINCREMENT,
                  trace_id        TEXT NOT NULL,
                  goal_id         TEXT NOT NULL,
                  kind            TEXT NOT NULL,
                  ts              INTEGER NOT NULL,
                  payload_json    TEXT NOT NULL
                );

                -- Self-observability: the deduplicated PROBLEMS catalog. One
                -- row per DISTINCT failure devclaw hits (fingerprinted by
                -- category + kind + normalized message), UPSERTed on recurrence
                -- so `count` grows while the table stays bounded — NOT a row per
                -- occurrence (the #250 lesson). Written ONLY by
                -- StateStore.record_problem (single writer), from the failure
                -- choke points. recovered_count vs terminal_count splits
                -- carried-past failures (a limit that auto-resumes) from
                -- terminal ones. The capture/dedup layer; the ranked report is
                -- a deliberate follow-up. See state_store/problems.py.
                -- Continuous-eval OUTCOME PROJECTION (ADR 0006): one row per
                -- settled evaluation sample. source='live' rows materialize
                -- inside the settle write itself (mark_done / mark_failed /
                -- mark_task_cancelled — the same single writer that owns task
                -- rows, sharing the settle commit, exactly-once); source=
                -- 'basket' rows land via `devclaw evals ingest` from
                -- measure_passrate report JSONs (idempotent on source +
                -- report_ref + ticket). failure_class is MECHANICAL string
                -- bucketing (rows.derive_failure_class) — never an LLM call.
                CREATE TABLE IF NOT EXISTS eval_outcomes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    source       TEXT NOT NULL CHECK (source IN ('live','basket')),
                    task_id      TEXT,              -- live: task uuid; basket: NULL ok
                    ticket       TEXT,              -- basket: basket ticket id; live: NULL
                    goal_id      TEXT,
                    kind         TEXT,              -- fix_bug | implement_feature | ...
                    workspace_dir TEXT,
                    status       TEXT NOT NULL,     -- done | failed | cancelled
                    verify_passed INTEGER,          -- 1/0, NULL = no gate ran
                    pr_url       TEXT,
                    attempts     INTEGER,
                    wall_ms      INTEGER,
                    failure_class TEXT,             -- short mechanical class, NULL when done
                    error        TEXT,              -- truncated (<=500 chars) raw error
                    report_ref   TEXT,              -- basket: report JSON filename
                    settled_at   INTEGER NOT NULL   -- epoch ms
                );

                -- Continuous-eval CYCLE REPORT (ADR 0006, tranche PR2): one row
                -- per per-cycle run-window that closed. Written from the layer-2
                -- heartbeat via record_cycle_report (the scheduled-edge owner),
                -- exactly-once per cycle_key (the PRIMARY KEY is the idempotency
                -- guard — INSERT OR IGNORE). `clean` is 1 iff zero mechanism-
                -- wedges fired in the window; wedges_json/pauses_json are the
                -- mechanical slice (never an LLM call). `sent_at` NULL = the
                -- notifier was unconfigured / the push didn't land (log-only,
                -- never an error). Read by the console Evals tab (PR3).
                -- PR ledger (spec 018 US2): ONE row per distinct PR devclaw
                -- opened — the PK on the URL is what makes distinct-PR
                -- counting a structural fact (goal-branch increments sharing
                -- a cumulative PR upsert one row). Created at the settle that
                -- first observes the URL; `state` is GROUND TRUTH read from
                -- the platform by the once-per-cycle refresh (the cycle-
                -- report step), never inferred from pr_url presence.
                -- state_as_of_ms NULL = never refreshed (reported as stale,
                -- never as current). States: open | merged | rejected | unknown.
                CREATE TABLE IF NOT EXISTS pr_ledger (
                    pr_url          TEXT PRIMARY KEY,
                    workspace_dir   TEXT,
                    opened_at_ms    INTEGER NOT NULL,
                    state           TEXT NOT NULL DEFAULT 'open',
                    state_as_of_ms  INTEGER
                );

                CREATE TABLE IF NOT EXISTS cycle_reports (
                    cycle_key      TEXT PRIMARY KEY,  -- YYYY-MM-DD of window OPEN, schedule tz
                    window_start_ms INTEGER NOT NULL,
                    window_end_ms   INTEGER NOT NULL,
                    clean           INTEGER NOT NULL,  -- 1 iff zero mechanism-wedges
                    -- `idle` (added 2026-08-07, migration below) = 1 iff the loop
                    -- did no work this cycle (off/held/all-cancelled): NEITHER
                    -- clean nor wedged, EXCLUDED from the clean-cycle rate.
                    wedges_json     TEXT NOT NULL,     -- JSON list [{class, detail, ref}]
                    pauses_json     TEXT NOT NULL,     -- self-healed pauses (reported, not wedges)
                    summary         TEXT NOT NULL,     -- the human-readable message body
                    sent_at         INTEGER,           -- NULL = notifier unconfigured (log-only)
                    created_at      INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS problems (
                  fingerprint     TEXT PRIMARY KEY,
                  category        TEXT NOT NULL,
                  kind            TEXT NOT NULL DEFAULT '',
                  summary         TEXT NOT NULL DEFAULT '',
                  sample_message  TEXT NOT NULL DEFAULT '',
                  count           INTEGER NOT NULL DEFAULT 0,
                  recovered_count INTEGER NOT NULL DEFAULT 0,
                  terminal_count  INTEGER NOT NULL DEFAULT 0,
                  first_seen_ms   INTEGER NOT NULL,
                  last_seen_ms    INTEGER NOT NULL,
                  last_goal_id    TEXT NOT NULL DEFAULT '',
                  last_task_id    TEXT NOT NULL DEFAULT ''
                );

                -- Cross-cycle survival of a problem (self-issue-filing Stage 1,
                -- proposal self-issue-filing.md O1). The `problems` catalog's
                -- raw `count` can't express "seen across N distinct run-cycles"
                -- — a burst of 50 in one night is one cycle, not N. This tiny
                -- membership table records (fingerprint, cycle_key) once per
                -- cycle a problem is active in; COUNT(DISTINCT cycle_key) is the
                -- recurrence signal that gates issue-filing (rescues the dead
                -- ops-agent O4 trend-repeat threshold). Bounded like `problems`
                -- (one row per problem per cycle), and prunes with the problem.
                CREATE TABLE IF NOT EXISTS problem_cycles (
                  fingerprint TEXT NOT NULL,
                  cycle_key   TEXT NOT NULL,
                  PRIMARY KEY (fingerprint, cycle_key)
                );

                -- Quiet-mode suppressed pings (spec 025 US3): every owner ping
                -- withheld while quiet mode is armed, in order, for the
                -- catch-up read on the operator's return. A record, never
                -- state — nothing reads it back for decisions.
                CREATE TABLE IF NOT EXISTS suppressed_pings (
                  id    INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts_ms INTEGER NOT NULL,
                  text  TEXT NOT NULL
                );

                -- Machine-filed issue ledger (spec 014) — the issue doorway's
                -- dedup source of truth: one row per (repo, fingerprint) a
                -- machine finding has ever been filed for. Local SQLite, not
                -- GitHub search, is authoritative (research.md D3): search is
                -- eventually consistent and rate-limited, and this instance is
                -- the single writer by construction. Distinct from `problems`
                -- on purpose: a problems row is a gatherer signal; this row is
                -- the filing record for ANY producer (catalog, deploy smoke,
                -- spec-015 validator).
                CREATE TABLE IF NOT EXISTS machine_issues (
                  repo             TEXT NOT NULL,
                  fingerprint      TEXT NOT NULL,
                  issue_number     INTEGER NOT NULL,
                  issue_state      TEXT NOT NULL,
                  schema_version   INTEGER NOT NULL,
                  source           TEXT NOT NULL,
                  occurrence_count INTEGER NOT NULL DEFAULT 1,
                  first_seen_ms    INTEGER NOT NULL,
                  last_seen_ms     INTEGER NOT NULL,
                  PRIMARY KEY (repo, fingerprint)
                );
                """
            )

            # (2) Forward-compat ALTERs for DBs created by older versions. Each
            # is idempotent — swallow duplicate-column errors. MUST run before
            # the indexes below, which reference these columns.
            for sql in (
                "ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'implement_feature'",
                "ALTER TABLE tasks ADD COLUMN notify_url TEXT",
                "ALTER TABLE tasks ADD COLUMN milestone TEXT",
                "ALTER TABLE tasks ADD COLUMN verify_cmd TEXT",
                "ALTER TABLE tasks ADD COLUMN deliver INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE tasks ADD COLUMN pr_url TEXT",
                "ALTER TABLE tasks ADD COLUMN title TEXT",
                # Durable goal-owner pointer (2026-07-04) — set by the goal
                # heartbeat when it dispatches a task; null for standalone
                # dispatch_task calls.
                "ALTER TABLE tasks ADD COLUMN parent_goal_id TEXT",
                # Usage-limit requeue counter (2026-07-10) — bounds the
                # pause→requeue→re-run loop (see Task.pause_count).
                "ALTER TABLE tasks ADD COLUMN pause_count INTEGER NOT NULL DEFAULT 0",
                # Generated-scaffolding flag (L3, #222) — a scaffold task skips
                # ONLY the adversarial review gate (verify gate + test-integrity
                # still run). Defaulted so pre-existing rows read as non-scaffold.
                "ALTER TABLE tasks ADD COLUMN scaffold INTEGER NOT NULL DEFAULT 0",
                # Planner-key of the PlannedTask this program-child row came
                # from (ADR 0003 stage 2) — for one-shot goals the key IS the
                # checklist item id, so the settle path can grade each item by
                # its own child task instead of the aggregate program verdict.
                # Null for standalone tasks and pre-existing rows.
                "ALTER TABLE tasks ADD COLUMN plan_key TEXT",
                # Durable gate-baseline (2026-07-19) — the pre-run HEAD captured
                # at the task's FIRST attempt. A pause→requeue re-run must diff
                # against THIS, not re-capture HEAD: by resume time HEAD is the
                # wip snapshot commit — the half-done work itself, not the base
                # (closeloop-bench b6d53bbd). Null for rows that predate the
                # column or never ran.
                "ALTER TABLE tasks ADD COLUMN pre_run_sha TEXT",
                # Gate strictness dial snapshotted at dispatch (ADR 0007) — the
                # settle cascade reads it to decide a dial-able gate failure's
                # consequence (strict blocks / trust advises-and-ships).
                # Defaulted so pre-existing rows read as advisory ("trust").
                "ALTER TABLE tasks ADD COLUMN strictness TEXT NOT NULL DEFAULT 'trust'",
                # Self-issue-filing Stage 1 (proposal self-issue-filing.md O2):
                # the GitHub issue this problem was filed as, so filing is
                # idempotent (one issue per fingerprint) and the age-out pass can
                # find still-open issues to close. NULL issue_number = never
                # filed; issue_state ∈ {'open','closed'} tracks the last known
                # GitHub state so recurrence can reopen and age-out can close.
                "ALTER TABLE problems ADD COLUMN issue_number INTEGER",
                "ALTER TABLE problems ADD COLUMN issue_state TEXT",
                # Branch-target delivery seam wire (v1-helper-resurface P1,
                # PR-2): the caller-chosen PR base and the pinned delivery
                # branch a direct ``dispatch_task`` carries through to
                # ``prepare_workspace`` + ``deliver_change``. NULL on goal-path
                # rows, which pin neither → the remote default branch.
                "ALTER TABLE tasks ADD COLUMN base_branch TEXT",
                "ALTER TABLE tasks ADD COLUMN target_branch TEXT",
                # The owning project's reference key (#524 P3), stamped at
                # dispatch. Per-project override knobs resolve BY this id, not by
                # a normalized-workspace-path scan. NULL on goal-path rows (goals
                # carry their own project_id) and on a task with no owning
                # project → knobs fall to the devclaw-wide defaults.
                "ALTER TABLE tasks ADD COLUMN project_id TEXT",
                # Idle cycle flag (2026-08-07) — 1 iff the loop did no work in
                # the window (off/held/all-cancelled): excluded from the
                # clean-cycle rate so empty nights of an OFF devclaw don't drift
                # it toward a meaningless 100%. Defaulted 0 so pre-existing rows
                # read as non-idle (their `clean` value is unchanged).
                "ALTER TABLE cycle_reports ADD COLUMN idle INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    db.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists

            # (2b) One-time backfill of the `idle` flag onto cycle_reports rows
            # written before the column existed (2026-08-07). Pre-existing rows
            # default idle=0, so the empty nights of an OFF devclaw already logged
            # would keep counting as "clean" until they scroll out of the 30-row
            # window — leaving the drifted rate wrong NOW, not just going forward.
            # A row is retro-idle iff it recorded NO work: clean, no wedges, no
            # self-healed pauses, no needs-operator surfacing, and the summary's
            # "settled 0: 0 done, 0 failed" tail (the only settle-count signal the
            # row carries). Naturally idempotent — the `idle = 0` guard means a
            # second open touches nothing; safe because it only ever flips an
            # empty-clean row, never a row with real work/wedges/pauses.
            try:
                db.execute(
                    "UPDATE cycle_reports SET idle = 1 "
                    "WHERE idle = 0 AND clean = 1 "
                    "AND wedges_json = '[]' AND pauses_json = '[]' "
                    "AND summary LIKE '%settled 0: 0 done, 0 failed%' "
                    "AND summary NOT LIKE '%needs operator%'"
                )
            except sqlite3.OperationalError:
                pass  # table/column not present yet (fresh DB pre-CREATE) — nothing to backfill

            # (3) Indexes — safe now that all referenced columns exist.
            db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_kind       ON tasks(kind);
                CREATE INDEX IF NOT EXISTS idx_tasks_parent_goal ON tasks(parent_goal_id);
                CREATE INDEX IF NOT EXISTS idx_events_task      ON events(task_id, id);
                CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts);
                CREATE INDEX IF NOT EXISTS idx_traces_goal      ON traces(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_traces_trace     ON traces(trace_id, id);
                CREATE INDEX IF NOT EXISTS idx_traces_kind      ON traces(kind, id);
                CREATE INDEX IF NOT EXISTS idx_traces_ts        ON traces(ts);
                CREATE INDEX IF NOT EXISTS idx_problems_category ON problems(category);
                CREATE INDEX IF NOT EXISTS idx_problems_count    ON problems(count);
                CREATE INDEX IF NOT EXISTS idx_eval_outcomes_settled
                    ON eval_outcomes(settled_at);
                -- Exactly-once belts: one projection row per live task settle
                -- (the settle UPDATE's rowcount guard is the primary defense;
                -- this makes a re-insert structurally impossible), and re-
                -- ingesting the same basket report is a no-op, not duplicates.
                CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_outcomes_live_task
                    ON eval_outcomes(task_id) WHERE source = 'live';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_outcomes_basket
                    ON eval_outcomes(source, report_ref, ticket) WHERE source = 'basket';
                CREATE INDEX IF NOT EXISTS idx_cycle_reports_created
                    ON cycle_reports(created_at);
                """
            )
            # (4) Data repair: events.ts is MILLISECONDS, but runner-emitted
            # events (through 2026-08-30) carried seconds-scale time.time()
            # values, which read as 1970 and get deleted by the ms-cutoff
            # retention prune as "ancient". Naturally idempotent (repaired
            # rows leave the range), rides idx_events_ts, and self-heals any
            # straggler written by a lagging sandbox image. 10**12 ms is
            # 2001-09 — below every real ms value, above every seconds value.
            db.execute("UPDATE events SET ts = ts * 1000 WHERE ts > 0 AND ts < 1000000000000")
            # (5) Program-lane demolition (spec 022 US3's store tail). ORDER IS
            # LOAD-BEARING: the lane's zombie pending rows must be DELETED
            # while tasks.program_id still exists — that column was the only
            # thing the pending scan filtered them out by; drop the column
            # first and they become dispatchable. Indexes drop before their
            # columns (SQLite refuses to drop an indexed column). Every step
            # is idempotent: the DELETE matches nothing once the column is
            # gone (guarded by the try), IF EXISTS covers table/indexes, and
            # a DROP COLUMN on an absent column raises OperationalError which
            # is swallowed like the add-column idiom above.
            try:
                db.execute(
                    "DELETE FROM tasks WHERE status = 'pending' AND program_id IS NOT NULL"
                )
            except sqlite3.OperationalError:
                pass  # program_id already dropped — zombies already gone
            db.executescript(
                """
                DROP INDEX IF EXISTS idx_tasks_program;
                DROP INDEX IF EXISTS idx_events_program;
                DROP INDEX IF EXISTS idx_programs_status;
                DROP INDEX IF EXISTS idx_programs_parent_goal;
                DROP TABLE IF EXISTS programs;
                """
            )
            for sql in (
                "ALTER TABLE tasks DROP COLUMN program_id",
                "ALTER TABLE tasks DROP COLUMN depends_on",
                "ALTER TABLE tasks DROP COLUMN order_idx",
                "ALTER TABLE tasks DROP COLUMN lane_json",
                "ALTER TABLE events DROP COLUMN program_id",
                "ALTER TABLE eval_outcomes DROP COLUMN program_id",
            ):
                try:
                    db.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already dropped (or never existed on this DB)
            commit()

    # ---- tasks ----------------------------------------------------------

