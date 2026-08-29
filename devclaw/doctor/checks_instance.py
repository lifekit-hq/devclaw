"""Instance-section doctor checks — deployed-instance invariants.

Every check is mechanical: filesystem stats, SQLite SELECTs over a READ-ONLY
connection, JSON parses. No cognition call, no subprocess, no write, ever
(spec 016 FR-004). Each function takes the shared context and returns at
least one Finding; the facade wraps crashes into ``unknown`` findings.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .. import claude_trust
from .. import config as _config
from ..goal.project_id_cutoff import CUTOFF_META_KEY as _PID_BACKFILL_KEY
from ..goal.store.legacy_cutoff import CUTOFF_META_KEY as _LEGACY_CUTOFF_KEY
from ..goal.store.view_migration import MIGRATION_META_KEY as _VIEW_MIGRATION_KEY
from ..state_store.trace_migration import MIGRATION_META_KEY as _TRACE_MIGRATION_KEY
from .model import Finding, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .context import InstanceContext

#: warn when the OAuth credential expires within this horizon.
_EXPIRY_WARN_MS = 48 * 3600 * 1000

#: the sandbox OAuth env var (one home: engine.sandcastle.OAUTH_TOKEN_VAR;
#: re-imported, not restated).
from ..engine.sandcastle import OAUTH_TOKEN_VAR as _OAUTH_TOKEN_VAR  # noqa: E402


def _ro_db(db_path: str) -> sqlite3.Connection:
    """Read-only connection — doctor structurally cannot write through it."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---- migrations / legacy shapes ------------------------------------------


def check_migration_meta_keys(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.migrations.meta_keys"
    hard = {
        _VIEW_MIGRATION_KEY: "goal view migration",
        _LEGACY_CUTOFF_KEY: "goal legacy cutoff (#616)",
        _TRACE_MIGRATION_KEY: "trace response_text migration",
    }
    missing = [name for key, name in hard.items() if not ctx.store.get_meta(key)]
    findings: list[Finding] = []
    if missing:
        findings.append(Finding(
            cid, Verdict.FAIL,
            f"one-shot migration marker(s) absent: {', '.join(sorted(missing))} — "
            "the DB predates its code or was replaced without a boot",
            remedy="restart devclaw (migrations stamp at boot)",
        ))
    else:
        findings.append(Finding(cid, Verdict.OK, "all construction-stamped migration markers present"))
    if not ctx.store.get_meta(_PID_BACKFILL_KEY):
        findings.append(Finding(
            cid, Verdict.WARN,
            "project_id backfill marker absent — this DB has never been booted "
            "by the server since the P3 re-key (pre-P3 goals stay unstamped)",
            remedy="start the devclaw server once against this DB",
        ))
    return findings


def check_legacy_goal_status_lifecycle(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.legacy.goal_status_lifecycle"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "goal_status" not in tables:
            return [Finding(cid, Verdict.OK, "goal_status table absent (no goals yet)")]
        n = db.execute(
            "SELECT COUNT(*) AS n FROM goal_status "
            "WHERE lifecycle IS NULL OR lifecycle != 'executing'"
        ).fetchone()["n"]
    if n:
        return [Finding(
            cid, Verdict.FAIL,
            f"{n} goal_status row(s) in a pre-008 lifecycle shape (NULL or non-'executing') — "
            "the legacy cutoff heal did not cover them",
            remedy="restart devclaw (legacy heal runs at boot); if it persists, inspect goal_status rows",
        )]
    return [Finding(cid, Verdict.OK, "every goal_status row carries lifecycle='executing'")]


def check_legacy_deliveries_ref_id(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.legacy.deliveries_ref_id"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "goal_deliveries" not in tables:
            return [Finding(cid, Verdict.OK, "goal_deliveries table absent (no deliveries yet)")]
        cols = {r["name"]: r for r in db.execute("PRAGMA table_info(goal_deliveries)")}
        ref = cols.get("ref_id")
        nullable = ref is not None and not ref["notnull"]
        n = db.execute("SELECT COUNT(*) AS n FROM goal_deliveries WHERE ref_id IS NULL").fetchone()["n"]
    if n or nullable:
        detail = []
        if nullable:
            detail.append("schema still allows NULL ref_id (pre-012 shape — the UNIQUE constraint is disabled by NULLs)")
        if n:
            detail.append(f"{n} delivery row(s) with NULL ref_id")
        return [Finding(cid, Verdict.FAIL, "; ".join(detail),
                        remedy="restart devclaw (legacy heal); persisting rows need manual inspection")]
    return [Finding(cid, Verdict.OK, "goal_deliveries.ref_id NOT NULL and no NULL rows")]


def check_legacy_dropped_shapes(ctx: "InstanceContext") -> list[Finding]:
    findings: list[Finding] = []
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "goal_docs" in tables:
            findings.append(Finding(
                "instance.legacy.goal_docs_table", Verdict.FAIL,
                "table goal_docs still present — the #616 cutoff should have dropped it",
                remedy="restart devclaw (legacy cutoff runs at boot)",
            ))
        else:
            findings.append(Finding("instance.legacy.goal_docs_table", Verdict.OK, "goal_docs table dropped"))
        if "goal_status" in tables:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(goal_status)")}
            if "inbox_ingest_cursor" in cols:
                findings.append(Finding(
                    "instance.legacy.inbox_cursor_column", Verdict.FAIL,
                    "column goal_status.inbox_ingest_cursor still present — pre-#616 shape",
                    remedy="restart devclaw (legacy cutoff runs at boot)",
                ))
            else:
                findings.append(Finding("instance.legacy.inbox_cursor_column", Verdict.OK,
                                        "inbox_ingest_cursor column dropped"))
        else:
            findings.append(Finding("instance.legacy.inbox_cursor_column", Verdict.OK,
                                    "goal_status table absent (no goals yet)"))
    return findings


# ---- auth (mechanical only — never invokes claude) -----------------------


def _find_expires_at(obj: Any) -> Optional[int]:
    """Recursively find the first numeric ``expiresAt`` in a parsed JSON blob."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "expiresAt" and isinstance(val, (int, float)):
                return int(val)
            found = _find_expires_at(val)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_expires_at(item)
            if found is not None:
                return found
    return None


def check_auth_credentials_file(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.auth.credentials_file"
    claude_dir = Path(_config.host_claude_dir())
    if not claude_dir.exists():
        return [Finding(
            cid, Verdict.UNKNOWN,
            f"claude dir {claude_dir} not visible from this process "
            "(devclaw containerized? DEVCLAW_HOST_CLAUDE_DIR names a host-only path)",
        )]
    cred = claude_dir / ".credentials.json"
    if not cred.exists():
        return [Finding(
            cid, Verdict.FAIL,
            f"{cred} absent — no /login credential on this host",
            remedy="vps-relogin (or `claude /login` on the box)",
        )]
    try:
        payload = json.loads(cred.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Finding(cid, Verdict.UNKNOWN, f"{cred} unreadable/unparseable: {exc!r}")]
    expires = _find_expires_at(payload)
    if expires is None:
        return [Finding(cid, Verdict.WARN, f"{cred} present but carries no expiresAt field")]
    now_ms = int(time.time() * 1000)
    if expires <= now_ms:
        return [Finding(cid, Verdict.FAIL,
                        "OAuth credential expired (expiresAt in the past)",
                        remedy="vps-relogin")]
    if expires - now_ms < _EXPIRY_WARN_MS:
        hours = (expires - now_ms) // 3600000
        return [Finding(cid, Verdict.WARN,
                        f"OAuth credential expires in ~{hours}h",
                        remedy="vps-relogin before it lapses")]
    return [Finding(cid, Verdict.OK, "OAuth credential present, expiry beyond 48h")]


def check_auth_claude_json(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.auth.claude_json"
    path = Path(claude_trust.config_path_for())
    if not path.exists():
        return [Finding(cid, Verdict.WARN, f"{path} absent — claude has never run here",
                        remedy="vps-relogin")]
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Finding(cid, Verdict.FAIL, f"{path} unparseable: {exc!r}",
                        remedy="vps-relogin (recreates the identity file)")]
    if not cfg.get("oauthAccount"):
        return [Finding(cid, Verdict.WARN, f"{path} carries no oauthAccount identity",
                        remedy="vps-relogin")]
    return [Finding(cid, Verdict.OK, "claude identity file parseable with oauthAccount")]


def check_auth_setup_token(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.auth.setup_token"
    present = bool(os.environ.get(_OAUTH_TOKEN_VAR, "").strip())
    # presence only — the value is never echoed into a report.
    if present:
        return [Finding(cid, Verdict.OK, f"{_OAUTH_TOKEN_VAR} set (sandbox auth rides the setup-token)")]
    return [Finding(cid, Verdict.OK, f"{_OAUTH_TOKEN_VAR} not set (sandbox auth rides the mounted /login credential)")]


def check_auth_pause(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.auth.pause"
    until_ms, reason = ctx.store.global_pause()
    now_ms = int(time.time() * 1000)
    if until_ms > now_ms:
        mins = (until_ms - now_ms) // 60000
        return [Finding(cid, Verdict.WARN,
                        f"usage pause active for ~{mins} more min: {reason or 'no reason recorded'}",
                        remedy="clear_usage_pause (only after fixing the cause)")]
    return [Finding(cid, Verdict.OK, "no usage/auth pause active")]


# ---- skills bundle -------------------------------------------------------


def check_skills_bundle(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.skills.bundle"
    # dynamic import: the canonical resolver lives in the (deliberately
    # standalone, spec 011) runner — reusing it instead of forking the glob
    # logic is the point; unimportable here degrades to unknown, never to a
    # silently-passing reimplementation.
    try:
        import importlib

        _runner = importlib.import_module("runner.runner")
        _skill_paths_for_root = _runner._skill_paths_for_root
        _KNOWN_KINDS = _runner._KNOWN_KINDS
    except Exception as exc:
        return [Finding(cid, Verdict.UNKNOWN,
                        f"runner resolver unimportable from this process: {exc!r}")]
    from ..engine.host import SKILLS_DIR
    if not os.path.isdir(SKILLS_DIR):
        return [Finding(cid, Verdict.FAIL,
                        f"skills root {SKILLS_DIR} is not a directory — every dispatch would "
                        "fail loud with skills_missing (#613)",
                        remedy="restore runner/skills (redeploy the image/checkout)")]
    empty = sorted(kind for kind in _KNOWN_KINDS if not _skill_paths_for_root(SKILLS_DIR, kind))
    if empty:
        return [Finding(cid, Verdict.FAIL,
                        f"bundle at {SKILLS_DIR} resolves NO files for kind(s): {', '.join(empty)}",
                        remedy="restore runner/skills (redeploy the image/checkout)")]
    return [Finding(cid, Verdict.OK,
                    f"bundle at {SKILLS_DIR} resolves files for all {len(_KNOWN_KINDS)} kinds")]


# ---- run schedule / dispatch gate ----------------------------------------


def check_schedule_raw_key(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.schedule.raw_key"
    findings: list[Finding] = []
    raw = ctx.store.get_meta("run_schedule")
    if raw is None:
        findings.append(Finding(
            cid, Verdict.WARN,
            "meta key 'run_schedule' absent — no window has ever been set on this DB "
            "(or it was lost with the DB on a redeploy); dispatch is ungated",
            remedy="set_run_schedule",
        ))
    else:
        try:
            sched = json.loads(raw)
            findings.append(Finding(
                cid, Verdict.OK,
                "global window stored: enabled={enabled} {start}-{end} {tz}".format(
                    enabled=sched.get("enabled"), start=sched.get("start"),
                    end=sched.get("end"), tz=sched.get("tz")),
            ))
        except Exception as exc:
            findings.append(Finding(
                cid, Verdict.FAIL,
                f"meta key 'run_schedule' corrupt ({exc!r}) — get_run_schedule silently "
                "falls back to the disabled default, so dispatch is ungated",
                remedy="set_run_schedule (rewrites the row)",
            ))
    prefix = ctx.store._GOAL_SCHEDULE_PREFIX
    for key in ctx.store.list_meta_keys(prefix=prefix):
        goal_id = key[len(prefix):]
        raw_goal = ctx.store.get_meta(key)
        try:
            json.loads(raw_goal or "")
        except Exception as exc:
            findings.append(Finding(
                cid, Verdict.FAIL,
                f"per-goal window '{key}' corrupt ({exc!r})",
                remedy=f"set_run_schedule (goal_id={goal_id})",
            ))
    return findings


def check_schedule_dispatch(ctx: "InstanceContext") -> list[Finding]:
    cid = "instance.schedule.dispatch"
    from ..dispatch_gate import operator_block
    blocked, why = operator_block(
        ctx.store.operator_hold(), ctx.store.get_run_schedule(), int(time.time() * 1000)
    )
    if blocked:
        return [Finding(cid, Verdict.OK, f"dispatch currently gated: {why} (informational)")]
    return [Finding(cid, Verdict.OK, "dispatch currently open (informational)")]


def check_goal_convergence_table(ctx: "InstanceContext") -> list[Finding]:
    """Spec 018 US1: the goal_convergence terminal ledger must exist wherever
    goal tables do — an instance whose DB predates the table silently reports
    every close as rounds-unknown, which the scorecard names but a fresh boot
    fixes (GoalState bootstraps tables at construction)."""
    cid = "instance.scorecard.goal_convergence"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "goal_status" not in tables:
        return [Finding(cid, Verdict.OK, "goal tables absent (no goals yet)")]
    if "goal_convergence" not in tables:
        return [Finding(
            cid, Verdict.FAIL,
            "goal_convergence table absent while goal tables exist — the DB "
            "predates spec 018; every goal close lands in the scorecard's "
            "rounds-unknown bucket until the table exists",
            remedy="restart devclaw (GoalState bootstraps tables at construction)",
        )]
    return [Finding(cid, Verdict.OK, "goal_convergence terminal ledger present")]


def check_pr_ledger(ctx: "InstanceContext") -> list[Finding]:
    """Spec 018 US2: the pr_ledger must exist (FAIL: DB predates the code —
    a boot bootstraps it), and a populated ledger that has NEVER been
    refreshed is surfaced (WARN: the scorecard's PR numbers all read
    unknown/stale until a cycle-report window closes)."""
    cid = "instance.scorecard.pr_ledger"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pr_ledger" not in tables:
            return [Finding(
                cid, Verdict.FAIL,
                "pr_ledger table absent — the DB predates spec 018 US2; PR "
                "ground truth cannot be recorded",
                remedy="restart devclaw (the store bootstraps tables at boot)",
            )]
        n = db.execute("SELECT COUNT(*) AS n FROM pr_ledger").fetchone()["n"]
    if n and not ctx.store.get_meta("pr_ledger_refresh"):
        return [Finding(
            cid, Verdict.WARN,
            f"pr_ledger holds {n} PR(s) but no refresh has ever run — the "
            "scorecard reports the ledger as stale until the next cycle-report "
            "window closes",
            remedy="wait for the next run-window close (the refresh rides it)",
        )]
    return [Finding(cid, Verdict.OK, "pr_ledger present" + (" and refreshed" if n else " (empty)"))]


def check_project_sandbox_sizing(ctx: "InstanceContext") -> list[Finding]:
    """Spec 020 US4 (per the spec-016 FR-014 convention: a registry schema
    change ships its doctor check): every stored per-project sizing override
    must still parse AND remain admittable against THIS host's MemTotal — a
    host shrink after a valid write is exactly the drift class doctor exists
    to catch (write-time validation saw a bigger machine)."""
    cid = "instance.sandbox.project_sizing"
    from ..host_resources import _parse_mem, host_mem_total_bytes

    reserve = _parse_mem(_config.COGNITION_MEM_RESERVE)
    total = host_mem_total_bytes()
    findings: list[Finding] = []
    for proj in ctx.registry.list():
        mem = getattr(proj, "sandbox_memory", None)
        if not mem:
            continue
        want = _parse_mem(mem)
        if total is not None and want + reserve > total:
            findings.append(Finding(
                cid, Verdict.FAIL,
                f"project {proj.id!r}: sandbox_memory {mem!r} is no longer "
                f"admittable on this host ({want} + reserve {reserve} > "
                f"MemTotal {total}) — its dispatches will defer forever",
                remedy=(
                    "lower the override (update_project sandbox_memory) or "
                    "grow the host"
                ),
            ))
    if findings:
        return findings
    return [Finding(cid, Verdict.OK, "per-project sandbox sizing overrides admittable")]


def check_goal_issue_identity_table(ctx: "InstanceContext") -> list[Finding]:
    """Spec 022 US1 (per spec-016 FR-014: a store-shape change ships its
    doctor check): the goal_issue_identity table enforces (project, issue)
    uniqueness for one_shot companion goals. An instance whose DB predates it
    can still dispatch, but the store-level uniqueness constraint is absent —
    a racing duplicate dispatch would create two goals. A server restart
    bootstraps the table idempotently."""
    cid = "instance.dispatch.goal_issue_identity"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "goal_status" not in tables:
        return [Finding(cid, Verdict.OK, "goal tables absent (no goals yet)")]
    if "goal_issue_identity" not in tables:
        return [Finding(
            cid, Verdict.FAIL,
            "goal_issue_identity table absent while goal tables exist — the DB "
            "predates spec 022; the store-level (project, issue) uniqueness "
            "constraint is not in place and a racing duplicate dispatch could "
            "create two goals for the same issue",
            remedy="restart devclaw (GoalState bootstraps tables at construction)",
        )]
    return [Finding(cid, Verdict.OK, "goal_issue_identity uniqueness table present")]


def check_merge_on_close_columns(ctx: "InstanceContext") -> list[Finding]:
    """Spec 025 US1 (per spec-016 FR-014: a store-shape change ships its
    doctor check): merge-on-close persists ``pending_merge_pr`` /
    ``merge_heal_attempted`` on goal_status. Two invariants: the columns
    exist (a DB predating spec 025 silently loses the owed-merge marker on
    restart), and no goal reads ``done`` while a merge is still owed —
    merge-on-close fires BEFORE the ACHIEVE transition, so that pair is a
    state the code must never produce."""
    cid = "instance.merge.close_columns"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "goal_status" not in tables:
            return [Finding(cid, Verdict.OK, "goal tables absent (no goals yet)")]
        cols = {r["name"] for r in db.execute("PRAGMA table_info(goal_status)")}
        missing = {"pending_merge_pr", "merge_heal_attempted"} - cols
        if missing:
            return [Finding(
                cid, Verdict.FAIL,
                f"goal_status column(s) absent: {', '.join(sorted(missing))} — the DB "
                "predates spec 025; an owed merge-on-close would be forgotten across "
                "a restart",
                remedy="restart devclaw (GoalState bootstraps columns at construction)",
            )]
        done_with_debt = [
            r["goal_id"] for r in db.execute(
                "SELECT goal_id FROM goal_status "
                "WHERE phase = 'done' AND COALESCE(pending_merge_pr, '') != ''"
            )
        ]
    if done_with_debt:
        return [Finding(
            cid, Verdict.FAIL,
            "goal(s) read done with a merge still owed (pending_merge_pr set): "
            + ", ".join(sorted(done_with_debt)),
            remedy="the goal branch was never merged — merge the PR by hand and "
                   "clear pending_merge_pr, then file the close-path bug",
        )]
    return [Finding(cid, Verdict.OK, "merge-on-close columns present; no done goal owes a merge")]


def check_suppressed_pings_table(ctx: "InstanceContext") -> list[Finding]:
    """Spec 025 US3 (per spec-016 FR-014): quiet mode records withheld pings
    into ``suppressed_pings``. A DB predating the table would make arming
    quiet mode silently DROP pings instead of recording them — the exact
    silent-degradation class quiet mode must never have."""
    cid = "instance.quiet.suppressed_pings"
    with _ro_db(ctx.store.db_path) as db:
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "suppressed_pings" not in tables:
        return [Finding(
            cid, Verdict.FAIL,
            "suppressed_pings table absent — the DB predates spec 025; arming "
            "quiet mode would drop pings instead of recording them",
            remedy="restart devclaw (StateStore bootstraps tables at construction)",
        )]
    return [Finding(cid, Verdict.OK, "suppressed_pings table present")]


INSTANCE_CHECKS: tuple = (
    check_migration_meta_keys,
    check_legacy_goal_status_lifecycle,
    check_legacy_deliveries_ref_id,
    check_legacy_dropped_shapes,
    check_auth_credentials_file,
    check_auth_claude_json,
    check_auth_setup_token,
    check_auth_pause,
    check_skills_bundle,
    check_schedule_raw_key,
    check_schedule_dispatch,
    check_goal_convergence_table,
    check_pr_ledger,
    check_project_sandbox_sizing,
    check_goal_issue_identity_table,
    check_merge_on_close_columns,
    check_suppressed_pings_table,
)
