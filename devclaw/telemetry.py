"""L8 scorecard telemetry — the rolling merge / steer / first-pass counters
plan.md §Measurement direction calls out as the "PR-by-PR delta on the scorecard
signals" surface.

Two ways in:

- ``compute_scorecard(store, window_hours=168)`` — a pure function over the
  state_store's ``tasks`` and ``traces`` tables. Cheap SQL + a light Python
  pass over the cognition ``response_text``; no cognition call.
- ``devclaw scorecard`` (CLI) and the ``get_scorecard_metrics`` MCP tool
  wrap the same function.

Deliberately *narrow* v1: skips the VPS-side dashboard render (separate infra)
and stays out of every path the goal engine actually runs — reading only. If a
metric can't be computed exactly from what the state store carries today, we
prefer a best-effort estimate with an explicit ``estimate_notes`` field over
inventing new persistence.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Optional

# ``role`` labels used by the cognition tracer for evaluator calls. See the
# ``role=`` arg on ``claude_with_model`` in ``goal/evaluator.py`` — the tracer
# stamps this into every CognitionEvent, so verdict counts can be scoped to
# the evaluator only.
_EVALUATOR_ROLE = "evaluator"

#: verdicts the evaluator can emit. See ``goal/evaluator._VALID_VERDICTS``;
#: reproduced here to avoid importing the evaluator (and its Anthropic caller)
#: into the telemetry module — telemetry stays a pure, dependency-light path.
_EVAL_VERDICTS = ("on_track", "off_track", "achieved", "stalled", "needs_human")

#: Best-effort verdict extractor. The tracer stores the FULL model response
#: as ``response_text``, so the verdict is found wherever it sits in the
#: response. A row that yields none (the model returned prose, or an error
#: string) lands under ``unparseable`` — that bucket holds only genuinely
#: verdict-less responses.
_VERDICT_RE = re.compile(r'"verdict"\s*:\s*"(\w+)"')

#: Same shape for the structural axis. Present only at done-gate responses;
#: absent from progress-check calls. Missing values are counted as ``"unknown"``
#: so the dashboard can spot non-done-gate calls without them polluting the
#: concrete grades.
_STRUCTURAL_RE = re.compile(r'"structural_health"\s*:\s*"(\w+)"')
_STRUCTURAL_GRADES = ("clean", "concerns", "poor")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_to_ms(s: Optional[str]) -> Optional[int]:
    """Best-effort ISO timestamp → epoch ms (goal tables store TEXT
    timestamps). Unparseable → None; the caller skips the row."""
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _extract_verdict(text: str) -> Optional[str]:
    """Pull the verdict string out of an evaluator ``response_text``. Returns
    None when the text doesn't look like an evaluator response (which happens
    for other cognition roles too — the caller filters by role first, but this
    stays defensive)."""
    if not text:
        return None
    m = _VERDICT_RE.search(text)
    if not m:
        return None
    v = m.group(1).strip().lower()
    return v if v in _EVAL_VERDICTS else None


def _extract_structural(text: str) -> Optional[str]:
    """Pull the ``structural_health`` grade — the axis-B verdict added by C3.
    Returns None when the field is absent (a progress-check call); only
    recognized values pass through."""
    if not text:
        return None
    m = _STRUCTURAL_RE.search(text)
    if not m:
        return None
    g = m.group(1).strip().lower()
    return g if g in _STRUCTURAL_GRADES else None


def _ws_norm(path: Optional[str]) -> Optional[str]:
    """Workspace path normalisation matching project_registry._normalize_workspace.
    No filesystem access — pure string ops."""
    if not path:
        return None
    p = str(path).strip()
    if not p:
        return None
    if p.startswith("~"):
        from pathlib import Path as _Path
        p = str(_Path(p).expanduser())
    while "//" in p:
        p = p.replace("//", "/")
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _empty_usage_bucket() -> dict:
    return {
        "cognition_tokens_in": 0,
        "cognition_tokens_out": 0,
        "cognition_rows_real": 0,
        "cognition_rows_estimated": 0,
        "cognition_cost_usd": 0.0,
        "worker_input_tokens": 0,
        "worker_output_tokens": 0,
        "worker_cache_read_tokens": 0,
        "worker_tasks_with_usage": 0,
        "worker_cost_usd": 0.0,
    }


def _accum_cognition(bucket: dict, p: dict) -> None:
    real_in = p.get("tokens_in")
    real_out = p.get("tokens_out")
    if real_in is not None or real_out is not None:
        bucket["cognition_rows_real"] += 1
        bucket["cognition_tokens_in"] += int(real_in or 0)
        bucket["cognition_tokens_out"] += int(real_out or 0)
    else:
        bucket["cognition_rows_estimated"] += 1
        bucket["cognition_tokens_in"] += int(p.get("tokens_in_est") or 0)
        bucket["cognition_tokens_out"] += int(p.get("tokens_out_est") or 0)
    c = p.get("cost_usd")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        bucket["cognition_cost_usd"] += float(c)


def _accum_worker(bucket: dict, usage: dict) -> None:
    bucket["worker_tasks_with_usage"] += 1
    for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
        v = usage.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            bucket[f"worker_{key}"] += int(v)
    c = usage.get("cost_usd")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        bucket["worker_cost_usd"] += float(c)


def _finalize_bucket(bucket: dict) -> dict:
    out = dict(bucket)
    out["cognition_cost_usd"] = round(out["cognition_cost_usd"], 6)
    out["worker_cost_usd"] = round(out["worker_cost_usd"], 6)
    out["cost_estimate_usd"] = round(out["cognition_cost_usd"] + out["worker_cost_usd"], 6)
    return out


_CAP_WINDOW_MS = 30 * 24 * 60 * 60 * 1000  # 30 days


def _build_cap_pressure(limit_rows: list, *, since_ms: int) -> dict:
    """Summarise limit-category problems into rate_limit / quota / auth groups.

    Each group carries the most-recent ``last_seen_ms`` and the stated
    ``reset_hint_s`` (from the provider's own message) when present.
    Only problems seen since ``since_ms`` are included.
    """
    from devclaw.loom.limits import classify_failure

    groups: dict[str, dict] = {}
    for row in limit_rows:
        lsm = row.get("last_seen_ms") or 0
        if lsm < since_ms:
            continue
        text = row.get("sample_message") or row.get("kind") or ""
        try:
            c = classify_failure(text)
        except Exception:
            continue
        kind_str = c.kind.value
        if kind_str not in ("rate_limit", "quota", "auth"):
            continue
        existing = groups.get(kind_str)
        if existing is None or lsm > existing["last_seen_ms"]:
            groups[kind_str] = {
                "last_seen_ms": int(lsm),
                "_hint": int(c.retry_after_s) if (c.stated and c.retry_after_s) else None,
            }

    result = {}
    for kind_str, entry in groups.items():
        e: dict = {"last_seen_ms": entry["last_seen_ms"]}
        if entry["_hint"] is not None:
            e["reset_hint_s"] = entry["_hint"]
        result[kind_str] = e
    return result


def compute_instance_usage(store: Any, registry: Any, all_goals: list) -> dict:
    """Instance-wide usage aggregate for the read-only GET /usage.json endpoint.

    Pure SQLite read — no LLM call, no subprocess, cheap enough to poll.

    Attribution:
      - Cognition traces via ``traces.goal_id`` → goal ``workspace_dir`` → project.
      - Worker tasks via ``tasks.workspace_dir`` → project directly.
      - Usage matching no registered project lands in ``unattributed``.

    ``registry`` is a :class:`~devclaw.project_registry.ProjectRegistry`;
    ``all_goals`` is the list returned by ``goal_service.list_goals()``.
    Both are typed ``Any`` to keep this module import-light (same pattern as
    ``compute_scorecard``).
    """
    # goal_id → normalised workspace_dir (covers every goal, incl. done/cancelled)
    goal_ws: dict[str, Optional[str]] = {}
    for g in all_goals:
        gid = g.get("id")
        if gid:
            goal_ws[gid] = _ws_norm(g.get("workspace_dir"))

    # normalised workspace_dir → (project_id, project_name)
    projects = list(registry.list())
    proj_ws: dict[str, tuple] = {}
    for p in projects:
        ws = _ws_norm(p.workspace_dir)
        if ws:
            proj_ws[ws] = (p.id, p.name)

    # Fetch all data in one lock acquisition.
    cap_since_ms = _now_ms() - _CAP_WINDOW_MS
    with store._lock:  # noqa: SLF001 — telemetry co-designs with state_store
        cog_rows = store._db.execute(
            "SELECT goal_id, payload_json FROM traces WHERE kind = 'cognition'"
        ).fetchall()
        task_rows = store._db.execute(
            "SELECT workspace_dir, result_json FROM tasks WHERE result_json IS NOT NULL"
        ).fetchall()
        limit_rows = store._db.execute(
            "SELECT kind, sample_message, last_seen_ms FROM problems WHERE category = 'limit'"
        ).fetchall()

    # Buckets: one per registered project + instance totals + unattributed.
    proj_buckets: dict[str, dict] = {p.id: _empty_usage_bucket() for p in projects}
    totals = _empty_usage_bucket()
    unattributed = _empty_usage_bucket()

    for row in cog_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        _accum_cognition(totals, payload)
        gid = row["goal_id"]
        ws = goal_ws.get(gid) if gid else None
        info = proj_ws.get(ws) if ws else None
        bucket = proj_buckets.get(info[0]) if info else None
        _accum_cognition(bucket if bucket is not None else unattributed, payload)

    for row in task_rows:
        try:
            usage = json.loads(row["result_json"]).get("usage")
        except (TypeError, ValueError):
            continue
        if not isinstance(usage, dict):
            continue
        _accum_worker(totals, usage)
        ws = _ws_norm(row["workspace_dir"])
        info = proj_ws.get(ws) if ws else None
        bucket = proj_buckets.get(info[0]) if info else None
        _accum_worker(bucket if bucket is not None else unattributed, usage)

    by_project = []
    for p in projects:
        b = _finalize_bucket(proj_buckets[p.id])
        b["project_id"] = p.id
        b["project_name"] = p.name
        by_project.append(b)

    fin_totals = _finalize_bucket(totals)
    fin_totals["cost_is_estimate"] = True

    limit_list = [
        {"kind": r["kind"], "sample_message": r["sample_message"], "last_seen_ms": r["last_seen_ms"]}
        for r in limit_rows
    ]
    return {
        "computed_at_ms": _now_ms(),
        "totals": fin_totals,
        "by_project": by_project,
        "unattributed": _finalize_bucket(unattributed),
        "cap_pressure": _build_cap_pressure(limit_list, since_ms=cap_since_ms),
    }


def sum_task_usage(result_jsons: Any) -> dict:
    """Sum the worker ``usage`` blocks out of task ``result_json`` payloads.

    Input is any iterable of raw ``result_json`` strings (None/torn entries
    are skipped — usage is telemetry, and absence is normal for a run that
    failed before the worker reported any). Returns the flat totals plus
    ``tasks_with_usage`` so a reader can tell "0 because free" from "0 because
    nothing reported".
    """
    totals = {
        "tasks_with_usage": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
    }
    for raw in result_jsons:
        if not raw:
            continue
        try:
            usage = json.loads(raw).get("usage")
        except (TypeError, ValueError):
            continue
        if not isinstance(usage, dict):
            continue
        totals["tasks_with_usage"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
            v = usage.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                totals[key] += int(v)
        c = usage.get("cost_usd")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            totals["cost_usd"] += float(c)
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def compute_scorecard(store: Any, *, window_hours: "int | None" = None, registry: Any = None) -> dict:
    """Roll up L8 scorecard metrics over the last ``window_hours``.

    ``store`` is a ``devclaw.state_store.StateStore`` — typed as ``Any`` here
    to keep the telemetry module import-light (no circular pull-in with the
    goal layer). ``registry`` (a ProjectRegistry, optional) supplies the
    bench-project marking (spec 018 US2): bench workspaces are reported
    separately and excluded from every ratchet-facing rate; with no registry
    nothing is bench. Pure store read — the pr_ledger's platform state was
    written by the once-per-cycle refresh, never here.
    """
    from . import config as _config  # local import: telemetry stays light at module load

    if window_hours is None:
        # the default display window IS the ratchet window (spec 018 US4) —
        # the gate and the numbers it grades are read over one span.
        window_hours = _config.ratchet_window_days() * 24
    since_ms = _now_ms() - int(window_hours * 3600 * 1000)
    bench_ws: set = set()
    if registry is not None:
        try:
            bench_ws = {
                _ws_norm(p.workspace_dir)
                for p in registry.list()
                if getattr(p, "bench", False) and p.workspace_dir
            }
        except Exception:  # noqa: BLE001 — a registry hiccup never breaks the read
            bench_ws = set()

    # ---- tasks + merge rate --------------------------------------------
    with store._lock:  # noqa: SLF001 — telemetry co-designs with state_store
        by_status = dict(
            store._db.execute(
                "SELECT status, COUNT(*) AS n FROM tasks "
                "WHERE completed_at IS NOT NULL AND completed_at >= ? "
                "GROUP BY status",
                (since_ms,),
            ).fetchall()
        )
        try:
            ledger_rows = store._db.execute(
                "SELECT pr_url, workspace_dir, opened_at_ms, state, state_as_of_ms "
                "FROM pr_ledger WHERE opened_at_ms >= ?",
                (since_ms,),
            ).fetchall()
            ledger_present = True
        except sqlite3.OperationalError:
            ledger_rows, ledger_present = [], False
        refresh_meta_row = None
        try:
            refresh_meta_row = store._db.execute(
                "SELECT value FROM meta WHERE key = 'pr_ledger_refresh'"
            ).fetchone()
        except sqlite3.OperationalError:
            pass

        # ---- worker usage (per-task result_json "usage" blocks) ---------
        usage_rows = store._db.execute(
            "SELECT result_json FROM tasks "
            "WHERE completed_at IS NOT NULL AND completed_at >= ? "
            "AND result_json IS NOT NULL",
            (since_ms,),
        ).fetchall()

        # ---- workspace breaks tripped in window ------------------------
        breaks_row = store._db.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE type = 'workspace_break_tripped' AND ts >= ?",
            (since_ms,),
        ).fetchone()
        workspace_breaks = int(breaks_row["n"] if breaks_row else 0)

        # ---- evaluator calls + verdict distribution --------------------
        # Traces don't index by role; a small window means a full scan is fine.
        # Filter to kind='cognition' at SQL level, then to role='evaluator' in
        # Python (role lives inside payload_json — no dedicated column).
        cog_rows = store._db.execute(
            "SELECT payload_json FROM traces "
            "WHERE kind = 'cognition' AND ts >= ? "
            "ORDER BY id ASC",
            (since_ms,),
        ).fetchall()

    worker_usage = sum_task_usage(r["result_json"] for r in usage_rows)

    verdicts: dict[str, int] = {v: 0 for v in _EVAL_VERDICTS}
    structural: dict[str, int] = {g: 0 for g in _STRUCTURAL_GRADES}
    unparseable = 0
    eval_calls = 0
    cog_tokens_in = 0
    cog_tokens_out = 0
    cog_cost_usd = 0.0
    for r in cog_rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        # Cognition usage sums span EVERY in-window cognition call (planner,
        # evaluator, gates…) — same real-usage-else-estimate
        # preference as StateStore.trace_totals. The evaluator filter below
        # applies only to verdict counting.
        if p.get("tokens_in") is not None or p.get("tokens_out") is not None:
            cog_tokens_in += int(p.get("tokens_in") or 0)
            cog_tokens_out += int(p.get("tokens_out") or 0)
        else:
            cog_tokens_in += int(p.get("tokens_in_est") or 0)
            cog_tokens_out += int(p.get("tokens_out_est") or 0)
        c = p.get("cost_usd")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            cog_cost_usd += float(c)
        if p.get("role") != _EVALUATOR_ROLE:
            continue
        eval_calls += 1
        text = p.get("response_text") or ""
        v = _extract_verdict(text)
        if v is None:
            unparseable += 1
            continue
        verdicts[v] += 1
        # Structural grade is present only at done-gate responses. Absent
        # elsewhere — don't inflate the denominator by counting misses.
        g = _extract_structural(text)
        if g is not None:
            structural[g] += 1

    total_terminal = int(sum(by_status.values()))
    done_count = int(by_status.get("done", 0))
    failed_count = int(by_status.get("failed", 0))
    cancelled_count = int(by_status.get("cancelled", 0))

    # ---- distinct-PR ground truth (spec 018 US2) -----------------------
    # PRs, never task rows (goal-branch increments share one PR and upsert
    # one ledger row); state is the platform's, stamped by the cycle refresh.
    # The verdict-free merged_with_pr/merge_rate this replaces counted "task
    # rows carrying a pr_url" — PR *opened*, never merged, with review tasks
    # polluting the denominator (audited 2026-08-25: 0.50 reported vs 11/13
    # decided = 0.85 ground truth).
    def _pr_bucket() -> dict:
        return {"opened": 0, "merged": 0, "rejected": 0, "open": 0, "unknown": 0}

    pr_main = _pr_bucket()
    pr_bench = _pr_bucket()
    for r in ledger_rows:
        bucket = pr_bench if (_ws_norm(r["workspace_dir"]) in bench_ws) else pr_main
        bucket["opened"] += 1
        st = r["state"] if r["state"] in ("merged", "rejected", "open", "unknown") else "unknown"
        bucket[st] += 1
    decided = pr_main["merged"] + pr_main["rejected"]
    refresh_at_ms = None
    refresh_truncated = False
    if refresh_meta_row and refresh_meta_row["value"]:
        try:
            meta = json.loads(refresh_meta_row["value"])
            refresh_at_ms = meta.get("at_ms")
            refresh_truncated = bool(meta.get("truncated"))
        except (TypeError, ValueError):
            pass
    pr_block = {
        **pr_main,
        "decided_merge_rate": (round(pr_main["merged"] / decided, 4) if decided else None),
        "state_as_of_ms": refresh_at_ms,   # null = ledger never refreshed → STALE
        "refresh_truncated": refresh_truncated,
        "bench": pr_bench,
    }
    pr_note = None if ledger_present else (
        "pr_ledger table absent (DB predates spec 018 US2) — PR ground truth "
        "unknown for this window."
    )

    # ---- per-goal convergence (spec 018 US1) ---------------------------
    # Goal-weighted, from the goal_convergence terminal ledger — the
    # verdict-weighted first_pass_hit_rate this replaces let one churny
    # goal shift a whole week (audited 2026-08-25: 0.36 reported vs 0.45
    # per-goal). Goal tables share this store's sqlite file (Tranche 1);
    # a DB predating the table degrades to an explicit note, never to
    # silent zeros.
    convergence: dict[str, Any] = {
        "goals_closed": 0, "first_pass": 0, "first_pass_rate": None,
        "rounds_median": None, "rounds_max": None,
        "abandoned": 0, "rounds_unknown": 0,
    }
    convergence_note: Optional[str] = None
    try:
        with store._lock:
            conv_rows = store._db.execute(
                "SELECT goal_id, outcome, rounds, workspace_dir, closed_at "
                "FROM goal_convergence"
            ).fetchall()
            term_rows = store._db.execute(
                "SELECT goal_id, at FROM goal_phase_history "
                "WHERE phase IN ('done', 'cancelled')"
            ).fetchall()
    except sqlite3.OperationalError:
        conv_rows, term_rows = [], []
        convergence_note = (
            "goal_convergence/goal_phase_history tables absent (DB predates "
            "spec 018) — per-goal convergence unknown for this window."
        )
    achieved_rounds: list[int] = []
    recorded_ids = {r["goal_id"] for r in conv_rows}
    for r in conv_rows:
        ms = _iso_to_ms(r["closed_at"])
        if ms is None or ms < since_ms:
            continue
        if _ws_norm(r["workspace_dir"]) in bench_ws:
            continue  # bench goals move no ratchet-facing number (SC-006)
        if r["outcome"] == "achieved":
            achieved_rounds.append(int(r["rounds"]))
        elif r["outcome"] == "abandoned":
            convergence["abandoned"] += 1
    unknown_ids = set()
    for r in term_rows:
        ms = _iso_to_ms(r["at"])
        if ms is None or ms < since_ms:
            continue
        if r["goal_id"] not in recorded_ids:
            unknown_ids.add(r["goal_id"])  # pre-018 close: never guessed
    convergence["rounds_unknown"] = len(unknown_ids)
    convergence["goals_closed"] = len(achieved_rounds)
    if achieved_rounds:
        # rounds counts every done proposal incl. the closing one, so
        # first-pass is rounds<=1 (0 covers a close with no verifying entry,
        # e.g. a manual evaluation path — it never proposed-and-failed).
        convergence["first_pass"] = sum(1 for n in achieved_rounds if n <= 1)
        convergence["first_pass_rate"] = round(
            convergence["first_pass"] / len(achieved_rounds), 4
        )
        convergence["rounds_median"] = statistics.median(achieved_rounds)
        convergence["rounds_max"] = max(achieved_rounds)

    # ---- cost per merged PR (the legibility number) ---------------------
    # Tokens are the honest unit on OAuth (Pro/Max) runs — the CLI reports no
    # dollar cost there, so cost_usd sums are often 0.0; report the token
    # ratio always and the dollar ratio only when a real cost was recorded.
    total_tokens = (
        cog_tokens_in + cog_tokens_out
        + worker_usage["input_tokens"] + worker_usage["output_tokens"]
    )
    total_cost_usd = round(cog_cost_usd + worker_usage["cost_usd"], 6)
    # denominator is now DISTINCT merged PRs (ground truth, non-bench) —
    # the name finally means what it says; null when nothing merged in-window.
    merged_prs = pr_main["merged"]
    tokens_per_merged_pr = (
        int(total_tokens / merged_prs) if merged_prs else None
    )
    cost_per_merged_pr_usd = (
        round(total_cost_usd / merged_prs, 6)
        if merged_prs and total_cost_usd > 0
        else None
    )

    # ---- steering split (spec 018 US3) ---------------------------------
    # HUMAN steering (owner-written rows, source not auto-*) counted from
    # where it already lives; the machine half is the convergence rounds
    # distribution — the single conflated steer_rate this replaces counted
    # only the machine's own off_track verdicts while wearing a name that
    # implied the owner.
    human_steers = 0
    steering_note = None
    try:
        with store._lock:
            hs_row = store._db.execute(
                "SELECT COUNT(*) AS n FROM goal_steering "
                "WHERE source NOT LIKE 'auto-%' AND created_at >= ?",
                (since_ms,),
            ).fetchone()
        human_steers = int(hs_row["n"] if hs_row else 0)
    except sqlite3.OperationalError:
        steering_note = (
            "goal_steering table absent (DB predates the goal tables) — "
            "human-steer count unknown for this window."
        )
    steering_block = {
        "human_steers": human_steers,
        "machine_correction_rounds_median": convergence["rounds_median"],
    }

    # ---- the finish line, machine-checked (spec 018 US4) ---------------
    # Pass/fail against the configured thresholds + the wedge-free-cycles
    # condition (non-idle cycle_reports rows in-window, all clean). A null
    # metric NEVER passes; the overall verdict is the AND. Informational
    # only — nothing actuates from it (spec 007's flip stays a human act).
    try:
        with store._lock:
            cyc_rows = store._db.execute(
                "SELECT clean, idle FROM cycle_reports WHERE window_end_ms >= ?",
                (since_ms,),
            ).fetchall()
    except sqlite3.OperationalError:
        cyc_rows = []
    counted = [r for r in cyc_rows if not (r["idle"] or 0)]
    clean_cycles = sum(1 for r in counted if r["clean"])
    thresholds = {
        "first_pass_rate": _config.ratchet_first_pass(),
        "decided_merge_rate": _config.ratchet_decided_merge(),
        "window_days": _config.ratchet_window_days(),
    }
    fp_val = convergence["first_pass_rate"]
    dm_val = pr_block["decided_merge_rate"]
    checks = {
        "first_pass_rate": {
            "value": fp_val,
            "pass": fp_val is not None and fp_val >= thresholds["first_pass_rate"],
        },
        "decided_merge_rate": {
            "value": dm_val,
            "pass": dm_val is not None and dm_val >= thresholds["decided_merge_rate"],
        },
        "wedge_free_window": {
            "clean_cycles": clean_cycles,
            "total_cycles": len(counted),
            "pass": len(counted) > 0 and clean_cycles == len(counted),
        },
    }
    ratchet = {
        "thresholds": thresholds,
        "checks": checks,
        "pass": all(c["pass"] for c in checks.values()),
    }

    return {
        "window_hours": window_hours,
        "since_ms": since_ms,
        "computed_at_ms": _now_ms(),
        "tasks": {
            "total_terminal": total_terminal,
            "done": done_count,
            "failed": failed_count,
            "cancelled": cancelled_count,
        },
        "pr": pr_block,
        "convergence": convergence,
        "steering": steering_block,
        "ratchet": ratchet,
        "workspace_breaks_tripped": workspace_breaks,
        "usage": {
            "cognition_tokens_in": cog_tokens_in,
            "cognition_tokens_out": cog_tokens_out,
            "cognition_cost_usd": round(cog_cost_usd, 6),
            "worker_input_tokens": worker_usage["input_tokens"],
            "worker_output_tokens": worker_usage["output_tokens"],
            "worker_cache_read_tokens": worker_usage["cache_read_tokens"],
            "worker_cost_usd": worker_usage["cost_usd"],
            "tasks_with_usage": worker_usage["tasks_with_usage"],
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
            "tokens_per_merged_pr": tokens_per_merged_pr,
            "cost_per_merged_pr_usd": cost_per_merged_pr_usd,
        },
        "evaluator": {
            "total_calls": eval_calls,
            "verdicts": verdicts,
            "unparseable_responses": unparseable,
            # Axis-B distribution — only counted for responses that carried a
            # structural_health field (post-C3 done-gate calls). Empty when no
            # evaluator response in-window reported one.
            "structural_grades": structural,
        },
        "estimate_notes": [
            n for n in (
                convergence_note,
                pr_note,
                steering_note,
                "usage: cognition rows without real CLI usage contribute their "
                "len/4 estimate; OAuth (Pro/Max) runs report no dollar cost, so "
                "tokens_per_merged_pr is the honest cross-billing number and "
                "cost_per_merged_pr_usd is null unless a real cost was recorded.",
            ) if n
        ],
    }


# ---- trace read surface (day-report + shared --since parsing) --------------
#
# Same philosophy as the scorecard above: pure SQL + a light Python pass over
# the SQL-narrowed rows, NO cognition call anywhere. The production traces
# table holds 200k+ rows — every query below filters in SQL first (kind/ts ride
# their indexes); Python only ever touches the in-window subset.


_SINCE_RE = re.compile(r"^(\d+)([mhd])$")
_SINCE_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}


def parse_since(spec: str, *, now_ms: Optional[int] = None) -> int:
    """Parse a ``--since`` spec into an epoch-ms lower bound.

    Accepts a relative window (``30m`` / ``24h`` / ``7d``) or an ISO-8601
    timestamp (naive → UTC, matching the epoch-ms ``ts`` the tracer writes).
    Raises ``ValueError`` on anything else — the CLI/HTTP callers turn that
    into a usage error instead of silently reading the whole table."""
    s = (spec or "").strip()
    m = _SINCE_RE.match(s)
    if m:
        base = now_ms if now_ms is not None else _now_ms()
        return base - int(m.group(1)) * _SINCE_UNIT_MS[m.group(2)]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(
            f"bad --since {spec!r}: use <N>m/<N>h/<N>d or an ISO timestamp"
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _error_class(error: str) -> str:
    """Deterministic error bucket: 'timeout' outranks everything (that's the
    class the owner greps for first), else the first line's prefix before the
    first ':' — devclaw error strings lead with their class ("spawn failed:",
    "review gate crashed:", "delivery failed:", ...)."""
    e = (error or "").strip()
    if not e:
        return "(none)"
    low = e.lower()
    if "timeout" in low or "timed out" in low:
        return "timeout"
    head = e.splitlines()[0].split(":", 1)[0].strip()
    return (head[:60] or "(unclassified)").lower()


def _percentile(sorted_values: list[int], q: float) -> int:
    """Nearest-rank percentile over an ascending list. Deterministic, no
    interpolation — report numbers must be reproducible byte-for-byte."""
    if not sorted_values:
        return 0
    rank = max(1, math.ceil(q * len(sorted_values)))
    return int(sorted_values[min(rank, len(sorted_values)) - 1])


def compute_trace_report(store: Any, *, since_ms: int) -> dict:
    """The 'what happened overnight' day-report: deterministic aggregates over
    ``tasks`` + ``traces`` since ``since_ms``. Reads only; NO LLM.

    Sections: tasks dispatched/settled by status + failed-task error classes,
    cognition calls by role (count / p50 / p90 / max latency, timeouts), retry
    storms (same task title attempted more than once), OWNER notifications,
    trend_check volume."""
    with store._lock:  # noqa: SLF001 — telemetry co-designs with state_store
        dispatched_row = store._db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE created_at >= ?",
            (since_ms,),
        ).fetchone()
        settled_by_status = dict(
            store._db.execute(
                "SELECT status, COUNT(*) AS n FROM tasks "
                "WHERE completed_at IS NOT NULL AND completed_at >= ? "
                "GROUP BY status",
                (since_ms,),
            ).fetchall()
        )
        failed_rows = store._db.execute(
            "SELECT error FROM tasks "
            "WHERE status = 'failed' AND completed_at IS NOT NULL "
            "AND completed_at >= ?",
            (since_ms,),
        ).fetchall()
        storm_rows = store._db.execute(
            "SELECT title, COUNT(*) AS n FROM tasks "
            "WHERE created_at >= ? AND title IS NOT NULL AND title != '' "
            "GROUP BY title HAVING n > 1 ORDER BY n DESC, title ASC",
            (since_ms,),
        ).fetchall()
        cog_rows = store._db.execute(
            "SELECT payload_json FROM traces "
            "WHERE kind = 'cognition' AND ts >= ? ORDER BY id ASC",
            (since_ms,),
        ).fetchall()
        notify_rows = store._db.execute(
            "SELECT COALESCE(json_extract(payload_json, '$.level'), '') AS lvl, "
            "COUNT(*) AS n FROM traces "
            "WHERE kind = 'notify' AND ts >= ? GROUP BY lvl",
            (since_ms,),
        ).fetchall()
        trend_total_row = store._db.execute(
            "SELECT COUNT(*) AS n FROM traces "
            "WHERE kind = 'trend_check' AND ts >= ?",
            (since_ms,),
        ).fetchone()
        trend_fired_row = store._db.execute(
            "SELECT COUNT(*) AS n FROM traces "
            "WHERE kind = 'trend_check' AND ts >= ? "
            "AND json_extract(payload_json, '$.fired')",
            (since_ms,),
        ).fetchone()

    error_classes: dict[str, int] = {}
    for r in failed_rows:
        c = _error_class(r["error"] or "")
        error_classes[c] = error_classes.get(c, 0) + 1

    # Cognition by role — the role lives inside payload_json; the SQL above
    # already narrowed to in-window cognition rows, so this pass is bounded.
    by_role: dict[str, dict] = {}
    latencies: dict[str, list[int]] = {}
    for r in cog_rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        role = str(p.get("role") or "(unknown)")
        rec = by_role.setdefault(
            role, {"calls": 0, "errors": 0, "timeouts": 0},
        )
        rec["calls"] += 1
        err = str(p.get("error") or "")
        if err:
            rec["errors"] += 1
            if _error_class(err) == "timeout":
                rec["timeouts"] += 1
        latencies.setdefault(role, []).append(int(p.get("latency_ms") or 0))
    for role, vals in latencies.items():
        vals.sort()
        by_role[role]["latency_ms"] = {
            "p50": _percentile(vals, 0.50),
            "p90": _percentile(vals, 0.90),
            "max": vals[-1] if vals else 0,
        }

    notify_by_level = {str(r["lvl"] or "(unknown)"): int(r["n"]) for r in notify_rows}

    return {
        "since_ms": since_ms,
        "computed_at_ms": _now_ms(),
        "tasks": {
            "dispatched": int(dispatched_row["n"] if dispatched_row else 0),
            "settled_by_status": {k: int(v) for k, v in sorted(settled_by_status.items())},
            "failed_error_classes": dict(sorted(error_classes.items())),
        },
        "cognition": {
            "total_calls": sum(r["calls"] for r in by_role.values()),
            "by_role": {k: by_role[k] for k in sorted(by_role)},
        },
        "retry_storms": [
            {"title": str(r["title"]), "attempts": int(r["n"])} for r in storm_rows
        ],
        "notifications": {
            "owner": int(notify_by_level.get("OWNER", 0)),
            "by_level": notify_by_level,
        },
        "trend_checks": {
            "total": int(trend_total_row["n"] if trend_total_row else 0),
            "fired": int(trend_fired_row["n"] if trend_fired_row else 0),
        },
    }


def format_trace_report(rep: dict) -> str:
    """Render a trace report dict for human eyeballing on the terminal."""
    def _iso(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(
            timespec="seconds"
        )

    t = rep["tasks"]
    lines = [
        f"window:      since {_iso(rep['since_ms'])}",
        f"tasks:       dispatched {t['dispatched']}",
    ]
    settled = t["settled_by_status"]
    if settled:
        lines.append(
            "  settled:   "
            + ", ".join(f"{k} {v}" for k, v in settled.items())
        )
    else:
        lines.append("  settled:   (none)")
    if t["failed_error_classes"]:
        lines.append("  failed by error class:")
        for cls, n in t["failed_error_classes"].items():
            lines.append(f"    {cls:<28} {n}")
    c = rep["cognition"]
    lines.append(f"cognition:   {c['total_calls']} calls")
    for role, rec in c["by_role"].items():
        lat = rec.get("latency_ms") or {}
        lines.append(
            f"  {role:<12} calls {rec['calls']:<4} "
            f"p50 {lat.get('p50', 0)}ms  p90 {lat.get('p90', 0)}ms  "
            f"max {lat.get('max', 0)}ms  timeouts {rec['timeouts']}"
        )
    storms = rep["retry_storms"]
    if storms:
        lines.append("retry storms (same title attempted >1):")
        for s in storms:
            lines.append(f"  {s['attempts']}x  {s['title']}")
    else:
        lines.append("retry storms: (none)")
    lines.append(f"notify:      OWNER {rep['notifications']['owner']}")
    for lvl, n in sorted(rep["notifications"]["by_level"].items()):
        if lvl != "OWNER":
            lines.append(f"  {lvl:<12} {n}")
    tc = rep["trend_checks"]
    lines.append(f"trend checks: {tc['total']} ({tc['fired']} fired)")
    return "\n".join(lines)


def _format_pr_line(pr: dict) -> str:
    if not pr:
        return "PRs:              (no ledger)"
    rate = (
        f"{pr['decided_merge_rate'] * 100:.1f}%" if pr.get("decided_merge_rate") is not None
        else "n/a"
    )
    stale = ""
    if pr.get("state_as_of_ms") is None:
        stale = "  [STALE: ledger never refreshed]"
    elif pr.get("refresh_truncated"):
        stale = "  [refresh cap hit — counts bounded]"
    return (
        f"PRs (distinct):   {pr.get('opened', 0)} opened — "
        f"{pr.get('merged', 0)} merged / {pr.get('rejected', 0)} rejected / "
        f"{pr.get('open', 0)} open / {pr.get('unknown', 0)} unknown  →  "
        f"decided-merge {rate}{stale}"
    )


def format_scorecard(sc: dict) -> str:
    """Render a scorecard dict for human eyeballing on the terminal."""
    t = sc["tasks"]
    e = sc["evaluator"]
    lines = [
        f"window:           last {sc['window_hours']}h",
        f"tasks (terminal): {t['total_terminal']} "
        f"(done {t['done']}, failed {t['failed']}, cancelled {t['cancelled']})",
        _format_pr_line(sc.get("pr") or {}),
        f"workspace breaks: {sc['workspace_breaks_tripped']}",
        f"evaluator calls:  {e['total_calls']}  (unparseable {e['unparseable_responses']})",
        "verdicts:",
    ]
    for v in _EVAL_VERDICTS:
        lines.append(f"  {v:<14} {e['verdicts'][v]}")
    st = sc.get("steering") or {}
    med = st.get("machine_correction_rounds_median")
    lines.append(
        f"steering:         human {st.get('human_steers', 0)} steer(s) · "
        f"machine correction median {med if med is not None else 'n/a'} round(s)"
    )
    r = sc.get("ratchet") or {}
    if r:
        parts = []
        for name, chk in (r.get("checks") or {}).items():
            parts.append(f"{name} {'PASS' if chk.get('pass') else 'fail'}")
        verdict = "PASS — autonomy gate satisfied" if r.get("pass") else "fail"
        lines.append(f"ratchet:          {verdict}  ({'; '.join(parts)})")
    c = sc.get("convergence") or {}
    if c:
        fp = (
            f"{c['first_pass_rate'] * 100:.1f}%" if c.get("first_pass_rate") is not None
            else "n/a"
        )
        med = c.get("rounds_median")
        lines.append(
            f"convergence:      {c.get('goals_closed', 0)} goal(s) closed, "
            f"first-pass {fp} ({c.get('first_pass', 0)}), "
            f"rounds median {med if med is not None else 'n/a'} "
            f"max {c.get('rounds_max') if c.get('rounds_max') is not None else 'n/a'}"
        )
        if c.get("abandoned") or c.get("rounds_unknown"):
            lines.append(
                f"                  abandoned {c.get('abandoned', 0)}, "
                f"rounds-unknown {c.get('rounds_unknown', 0)} (pre-018 closes)"
            )
    struct = e.get("structural_grades") or {}
    if any(struct.values()):
        lines.append("structural (done-gate only):")
        for g in _STRUCTURAL_GRADES:
            lines.append(f"  {g:<14} {struct.get(g, 0)}")
    u = sc.get("usage") or {}
    if u:
        lines.append(
            f"usage:            cognition {u['cognition_tokens_in']}+{u['cognition_tokens_out']} tok, "
            f"workers {u['worker_input_tokens']}+{u['worker_output_tokens']} tok "
            f"({u['tasks_with_usage']} tasks reporting)"
        )
        per_pr = (
            f"{u['tokens_per_merged_pr']} tok" if u["tokens_per_merged_pr"] is not None else "n/a"
        )
        if u["cost_per_merged_pr_usd"] is not None:
            per_pr += f" / ${u['cost_per_merged_pr_usd']:.4f}"
        lines.append(f"per merged PR:    {per_pr}")
    lines.append("")
    lines.append("estimate notes:")
    for n in sc.get("estimate_notes") or []:
        lines.append(f"  - {n}")
    return "\n".join(lines)
