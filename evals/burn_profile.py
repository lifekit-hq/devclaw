"""Token-burn profile of worker tasks — where the context actually goes.

Walks a task's event stream in order, tracks the agent's own cumulative
usage (``usage_update.used``), and attributes each increase to the tool call
that preceded it. Pure mechanism over the events table: zero LLM, read-only,
runnable against any ``devclaw.db``.

Two modes:

    # one task, in detail (default: the newest implement_feature task)
    python evals/burn_profile.py [task_id_prefix]

    # the last N tasks, bucketed by tool and by command shape — what is
    # SYSTEMIC waste rather than one session's accident
    python evals/burn_profile.py --aggregate [N]

The DB is ``$DEVCLAW_DB`` or ``./devclaw.db``. On the deployed instance run it
inside the container (``docker cp`` + ``docker exec … python3``), where the
DB is ``/var/lib/devclaw/devclaw.db``.

Why this exists (2026-09-02): the worker was hitting the context wall every
session and every explanation offered — planning ceremony, whole-file
re-reads — was a hunch. Measuring 25 tasks said otherwise: zero redundant
whole-file reads; instead 57 separate greps of three files (~42k tokens) and
a single ``pytest`` invocation costing ~25k tokens against ~400 for an
equivalently filtered ``dotnet build``. Optimise from the profile, not the
story.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import sys

DB = os.environ.get("DEVCLAW_DB") or "devclaw.db"


def _norm(cmd: str) -> str:
    """Collapse a shell command to its shape so repeats are countable."""
    cmd = " ".join((cmd or "").split())
    cmd = re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", cmd)
    cmd = re.sub(r"\b\d+\b", "<n>", cmd)
    cmd = re.sub(r"'[^']{0,200}'", "'…'", cmd)
    cmd = re.sub(r'"[^"]{0,200}"', '"…"', cmd)
    return cmd[:100]


def _verb(cmd: str) -> str:
    """The command's verb — `npx ng test`, `git diff`, `sed -n` …"""
    parts = [p for p in (cmd or "").split() if "=" not in p]
    return " ".join(parts[:3])[:44] or "?"


def _events(conn: sqlite3.Connection, task_id: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for etype, pj in conn.execute(
        "SELECT type, payload_json FROM events WHERE task_id=? ORDER BY rowid",
        (task_id,),
    ):
        try:
            out.append((etype, json.loads(pj)))
        except Exception:  # noqa: BLE001 — a bad row is skipped, never fatal
            pass
    return out


def _resolve_calls(events: list[tuple[str, dict]]) -> dict[str, dict]:
    """toolCallId -> {tool, detail}. A tool call arrives as SEVERAL events
    (start, then tool_call_update) and only some carry ``rawInput``, so
    every event mentioning an id is merged."""
    info: dict[str, dict] = {}
    for etype, d in events:
        if etype != "ACPToolCallEvent":
            continue
        tcid = d.get("toolCallId")
        if not tcid:
            continue
        rec = info.setdefault(tcid, {"tool": None, "detail": None})
        name = ((d.get("_meta") or {}).get("claudeCode") or {}).get("toolName")
        if name:
            rec["tool"] = name
        raw = d.get("rawInput") or {}
        if raw.get("file_path"):
            rec["detail"] = raw["file_path"]
        elif raw.get("command"):
            rec["detail"] = _norm(raw["command"])
        elif raw.get("pattern"):
            rec["detail"] = f"pattern={raw['pattern']!r} path={raw.get('path', '')}"
    return info


def _attribute(
    events: list[tuple[str, dict]], info: dict[str, dict]
) -> tuple[int | None, int | None, collections.Counter]:
    """Walk in order; each rise in ``used`` is charged to the last tool call."""
    used = first = None
    last: tuple[str, str] | None = None
    attribution: collections.Counter = collections.Counter()
    for etype, d in events:
        if etype == "ACPToolCallEvent":
            tcid = d.get("toolCallId")
            if tcid in info:
                rec = info[tcid]
                last = (rec["tool"] or "?", rec["detail"] or "(input not captured)")
        elif etype == "ACPUpdateEvent" and d.get("sessionUpdate") == "usage_update":
            u = d.get("used")
            if not isinstance(u, int):
                continue
            if used is None:
                first = u
            elif u > used and last:
                attribution[last] += u - used
            used = u
    return first, used, attribution


def profile_one(conn: sqlite3.Connection, prefix: str | None) -> None:
    if prefix:
        row = conn.execute(
            "SELECT id, kind FROM tasks WHERE id LIKE ? ORDER BY created_at DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, kind FROM tasks WHERE kind='implement_feature' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        print("no matching task")
        return
    tid, kind = row
    events = _events(conn, tid)
    info = _resolve_calls(events)
    first, used, attribution = _attribute(events, info)

    calls: collections.Counter = collections.Counter()
    reads: collections.Counter = collections.Counter()
    bashes: collections.Counter = collections.Counter()
    for rec in info.values():
        t = rec["tool"] or "?"
        calls[t] += 1
        if t == "Read" and rec["detail"]:
            reads[rec["detail"]] += 1
        elif t == "Bash" and rec["detail"]:
            bashes[rec["detail"]] += 1

    print(f"task {tid[:12]}  kind={kind}")
    print(f"context: {first} -> {used}  (+{(used or 0) - (first or 0)})")
    print(f"tool calls: {dict(calls)}  total={sum(calls.values())}")

    redundant = sum(n - 1 for n in reads.values() if n > 1)
    print(f"\n-- re-read files ({redundant} redundant of {len(reads)} distinct) --")
    for p, n in reads.most_common(10):
        if n > 1:
            print(f"  {n:3}x  {p}")
    print("\n-- repeated shell commands --")
    for cmd, n in bashes.most_common(10):
        if n > 1:
            print(f"  {n:3}x  {cmd}")
    print("\n-- biggest context consumers --")
    for (t, detail), tok in attribution.most_common(15):
        print(f"  {tok:7}  {t:6} {str(detail)[:86]}")


def aggregate(conn: sqlite3.Connection, n: int) -> None:
    tasks = conn.execute(
        "SELECT id FROM tasks WHERE kind='implement_feature' "
        "ORDER BY created_at DESC LIMIT ?",
        (n,),
    ).fetchall()
    by_tool: collections.Counter = collections.Counter()
    by_shape: collections.Counter = collections.Counter()
    by_verb: collections.Counter = collections.Counter()
    hits: collections.Counter = collections.Counter()
    totals: list[tuple[int, int, str]] = []

    for (tid,) in tasks:
        events = _events(conn, tid)
        if not events:
            continue
        info = _resolve_calls(events)
        first, used, attribution = _attribute(events, info)
        task_total = 0
        for (t, detail), tok in attribution.items():
            by_tool[t] += tok
            task_total += tok
            if t == "Bash" and detail and detail != "(input not captured)":
                by_shape[detail] += tok
                hits[detail] += 1
                by_verb[_verb(detail)] += tok
        if task_total:
            totals.append((task_total, used or 0, tid[:12]))

    grand = sum(by_tool.values()) or 1
    print(f"profiled {len(totals)} implement_feature tasks\n")
    print("-- context growth by tool --")
    for t, tok in by_tool.most_common():
        print(f"  {tok:9}  {100 * tok / grand:5.1f}%  {t}")
    print("\n-- shell output cost by command verb --")
    for v, tok in by_verb.most_common(14):
        print(f"  {tok:8}  {100 * tok / grand:5.1f}%  {v}")
    print("\n-- worst command shapes (total tokens, times run) --")
    for shape, tok in by_shape.most_common(14):
        print(f"  {tok:7} x{hits[shape]:<3} {shape}")
    print("\n-- per-task growth --")
    for tot, end, tid in sorted(totals, reverse=True)[:12]:
        print(f"  +{tot:7}  ended at {end:7}  {tid}")


def main(argv: list[str]) -> int:
    if not os.path.exists(DB):
        print(f"no database at {DB} (set DEVCLAW_DB)", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if argv and argv[0] == "--aggregate":
        aggregate(conn, int(argv[1]) if len(argv) > 1 else 20)
    else:
        profile_one(conn, argv[0] if argv else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
