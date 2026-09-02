"""Owner-ping profile — spec 031's success metric (SC-001 / SC-002).

Pure read over ``goal_problems`` / ``goal_decisions``; zero LLM. Reports, for a
window: Problems raised (= owner pings) per goal-week, what resolved them —
``owner`` (a typed verb), ``defaulted`` (the timebox), ``admission`` (the
lint) — how many are still open, and how many were resolved by prose steering
(the one number that must be zero; a Problem closed while a steering row
landed in the same minute is counted as prose-resolved).

    python evals/ping_profile.py            # last 14 days
    python evals/ping_profile.py --days 7

The DB is ``$DEVCLAW_DB`` or ``./devclaw.db``; on the deployed instance run
it inside the container against ``/var/lib/devclaw/devclaw.db``.

Baseline 2026-09-02 (pre-spec-031): 8 pings in one day across 10 goals, ~1
needing judgement. SC-001: pings per goal-week at most half of that. SC-002:
the share needing judgement (``owner``-resolved, non-default option) above half.
"""

from __future__ import annotations

import collections
import os
import sqlite3
import sys
import time

DB = os.environ.get("DEVCLAW_DB") or "devclaw.db"


def main(argv: list[str]) -> int:
    days = 14
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    if not os.path.exists(DB):
        print(f"no database at {DB} (set DEVCLAW_DB)", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "goal_problems" not in tables:
        print("goal_problems absent — the instance predates spec 031")
        return 1
    since = int((time.time() - days * 86400) * 1000)

    probs = conn.execute(
        "SELECT * FROM goal_problems WHERE raised_at >= ? ORDER BY raised_at", (since,)
    ).fetchall()
    goals = {p["goal_id"] for p in probs}
    weeks = max(days / 7.0, 1e-9)
    by_raiser = collections.Counter(p["raised_by"] for p in probs)
    by_status = collections.Counter(p["status"] for p in probs)

    decs = {
        d["id"]: d for d in conn.execute(
            "SELECT * FROM goal_decisions WHERE made_at >= ?", (since,)
        ).fetchall()
    }
    by_prov = collections.Counter()
    judgement = 0
    for p in probs:
        d = decs.get(p["closed_by_decision"] or "")
        if d is None:
            continue
        by_prov[d["provenance"]] += 1
        if d["provenance"] == "owner" and (d["option_key"] or "") != (p["default_key"] or ""):
            judgement += 1  # the owner chose something other than the default

    prose = 0
    if "goal_steering" in tables:
        for p in probs:
            if p["closed_at"] is None:
                continue
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM goal_steering WHERE goal_id=? "
                "AND source NOT LIKE 'auto-%' AND ABS(? - COALESCE(created_at, 0)) < 60000",
                (p["goal_id"], p["closed_at"]),
            ).fetchone()["n"]
            prose += 1 if n else 0

    print(f"window: last {days} days  |  problems (= pings): {len(probs)}  |  goals touched: {len(goals)}")
    print(f"pings per goal-week: {len(probs) / weeks / max(len(goals), 1):.2f}   (SC-001 target: ≤ half of baseline)")
    print(f"needed judgement: {judgement} of {sum(by_prov.values()) or 1} resolved  "
          f"({100 * judgement / max(sum(by_prov.values()), 1):.0f}%; SC-002 target: > 50%)")
    print(f"resolved by prose steering: {prose}   (must be 0)")
    print("\nby raiser:   " + ", ".join(f"{k}={v}" for k, v in by_raiser.most_common()))
    print("by status:   " + ", ".join(f"{k}={v}" for k, v in by_status.most_common()))
    print("by resolver: " + ", ".join(f"{k}={v}" for k, v in by_prov.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
