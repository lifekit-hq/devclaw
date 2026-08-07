"""The compounding scorecard — does a durable goal build on itself across nights?

The instrument for the compounding experiment (docs/proposals/
compounding-experiment.md, P1 LOCKED #483). Each night, after the goal's work
merges, the hidden checklist (evals/ledger_checklist/) is run against the target
repo at HEAD → a criteria vector. This module records those vectors and reads
the trajectory across nights: is the green-count **monotonically non-decreasing
→ all-green** (compounding), or is it **churning** (green→red) / **plateauing**?

Design boundaries, on purpose:

* The store is an **experiment-owned JSONL log** (one line per night), NOT the
  core `devclaw.db` / StateStore. The scorecard is an offline instrument Denys
  runs by hand — it is not part of the heartbeat's append-only event stream, so
  it must not touch the single-writer-to-state invariant.
* The **analysis** (`analyze`) and the **store** are pure and stub-tested. The
  only live-box part is booting the target app to build a real `CheckCtx`; that
  lives in `main()` and is the operator's responsibility (documented), so the
  measurable logic here needs no toolchain.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.ledger_checklist.checklist import (
    CHECKS,
    Check,
    CheckCtx,
    criteria_vector,
    run_checklist,
)

# Trailing working-nights with no new green before we call it a plateau.
PLATEAU_K = 2


@dataclass
class NightRecord:
    """One graded night: the criteria vector + enough context to read it later."""

    night: int  # 1-based index in the run
    ts_ms: int  # when graded
    repo_ref: str  # the target-repo HEAD sha that was graded
    vector: dict[str, bool]  # criterion id -> green
    tokens: int | None = None  # tokens the goal spent that night (idle detection)
    notes: str = ""

    def green(self) -> set[str]:
        return {cid for cid, ok in self.vector.items() if ok}

    def green_count(self) -> int:
        return sum(1 for ok in self.vector.values() if ok)


# --- store (append-only JSONL, experiment-owned) ----------------------------

def append_night(path: str | Path, rec: NightRecord) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), sort_keys=True) + "\n")


def load_history(path: str | Path) -> list[NightRecord]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[NightRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(NightRecord(**d))
    out.sort(key=lambda r: r.night)
    return out


# --- analysis (pure) --------------------------------------------------------

@dataclass
class CompoundingReport:
    nights: int
    total_criteria: int
    latest_green: int
    new_green: list[str] = field(default_factory=list)  # turned green in the latest night
    churned: list[str] = field(default_factory=list)  # green→red in the latest transition
    monotonic: bool = True  # green-count non-decreasing across ALL nights
    plateau_nights: int = 0  # trailing nights (with work) that added no new green
    reached_all_green: bool = False
    verdict: str = "in-progress"  # closed | compounding | plateau | churn | started | in-progress


def _worked(rec: NightRecord) -> bool:
    """Did this night do work? Unknown (tokens is None) counts as worked — we
    only exclude a night from the plateau tail when we KNOW it was idle."""
    return rec.tokens is None or rec.tokens > 0


def analyze(history: list[NightRecord]) -> CompoundingReport:
    if not history:
        return CompoundingReport(nights=0, total_criteria=0, latest_green=0, verdict="no-data")

    latest = history[-1]
    total = len(latest.vector)
    counts = [r.green_count() for r in history]
    monotonic = all(counts[i] <= counts[i + 1] for i in range(len(counts) - 1))

    if len(history) == 1:
        new_green = sorted(latest.green())
        churned: list[str] = []
    else:
        prev = history[-2]
        new_green = sorted(latest.green() - prev.green())
        churned = sorted(prev.green() - latest.green())

    # Plateau: trailing WORKED nights that added no new green. Night 1's "new
    # green" is its own greens; a first night with greens is progress, not plateau.
    plateau = 0
    for i in range(len(history) - 1, 0, -1):
        if not _worked(history[i]):
            continue  # idle night doesn't count toward (or reset) a plateau
        added = history[i].green() - history[i - 1].green()
        if added:
            break
        plateau += 1

    reached = counts[-1] == total and total > 0

    if reached:
        verdict = "closed"
    elif churned:
        verdict = "churn"
    elif plateau >= PLATEAU_K:
        verdict = "plateau"
    elif len(history) == 1:
        verdict = "started"
    elif counts[-1] > counts[-2]:
        verdict = "compounding"
    else:
        verdict = "in-progress"

    return CompoundingReport(
        nights=len(history),
        total_criteria=total,
        latest_green=counts[-1],
        new_green=new_green,
        churned=churned,
        monotonic=monotonic,
        plateau_nights=plateau,
        reached_all_green=reached,
        verdict=verdict,
    )


# --- report rendering -------------------------------------------------------

_VERDICT_LINE = {
    "closed": "✅ CLOSED — all criteria green; the goal compounded to completion.",
    "compounding": "📈 COMPOUNDING — the green-count rose this night, building on prior work.",
    "in-progress": "… in progress — no new green this night, but nothing regressed.",
    "started": "▶ started — first graded night on record.",
    "plateau": "⚠ PLATEAU — no new green across the last {k}+ working nights.",
    "churn": "⛔ CHURN — a previously-green criterion went red this night ({churned}).",
    "no-data": "no nights recorded yet.",
}


def render_report(report: CompoundingReport, history: list[NightRecord]) -> str:
    if report.verdict == "no-data":
        return "compounding: no nights recorded yet."
    head = _VERDICT_LINE[report.verdict].format(
        k=PLATEAU_K, churned=", ".join(report.churned) or "—"
    )
    lines = [
        "# Compounding scorecard",
        "",
        head,
        "",
        f"- nights graded: **{report.nights}**",
        f"- criteria green: **{report.latest_green}/{report.total_criteria}**",
        f"- new green this night: {', '.join(report.new_green) or '—'}",
        f"- churned this night: {', '.join(report.churned) or '—'}",
        f"- monotonic (never regressed): **{report.monotonic}**",
        f"- plateau length: {report.plateau_nights}",
        "",
        "| night | green | Δ | new green | churned |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(history):
        prev = history[i - 1].green() if i else set()
        added = sorted(r.green() - prev)
        gone = sorted(prev - r.green())
        delta = r.green_count() - (history[i - 1].green_count() if i else 0)
        lines.append(
            f"| {r.night} | {r.green_count()}/{len(r.vector)} | "
            f"{'+' if delta >= 0 else ''}{delta} | "
            f"{', '.join(added) or '—'} | {', '.join(gone) or '—'} |"
        )
    return "\n".join(lines)


# --- one graded night (orchestration; stub-testable via an injected ctx) ----

def run_night(
    ctx: CheckCtx,
    *,
    night: int,
    repo_ref: str,
    checks: list[Check] = CHECKS,
    tokens: int | None = None,
    ts_ms: int | None = None,
) -> NightRecord:
    """Grade one night: run the hidden checklist against ``ctx`` and wrap the
    result in a ``NightRecord``. Pure orchestration — the toolchain lives behind
    ``ctx``, so this is exercised with a fake ctx off the box."""
    results = run_checklist(ctx, checks)
    return NightRecord(
        night=night,
        ts_ms=ts_ms if ts_ms is not None else int(time.time() * 1000),
        repo_ref=repo_ref,
        vector=criteria_vector(results),
        tokens=tokens,
    )


# --- live-box CLI (operator boots the app; see ledger_checklist/README) ------

def _live_ctx(repo: str, backend_url: str) -> CheckCtx:  # pragma: no cover - live only
    """A real CheckCtx over subprocess + urllib. LIVE-BOX ONLY: needs a
    .NET/Node toolchain and an already-booted backend at ``backend_url`` (the
    operator boots it per ledger_checklist/README.md's execution contract)."""
    import subprocess
    import urllib.error
    import urllib.request
    from evals.ledger_checklist.checklist import ApiResult, ShResult

    def sh(cmd, cwd=None, timeout=600):
        wd = str(Path(repo) / cwd) if cwd else repo
        try:
            p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=timeout)
            return ShResult(p.returncode, (p.stdout or "") + (p.stderr or ""))
        except Exception as e:  # noqa: BLE001
            return ShResult(1, f"{type(e).__name__}: {e}")

    def api(method, path, token=None, json_body=None, json=None):
        body = json if json is not None else json_body
        data = None
        headers = {}
        if body is not None:
            data = __import__("json").dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(backend_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return ApiResult(r.status, r.read().decode(errors="replace"))
        except urllib.error.HTTPError as e:
            return ApiResult(e.code, e.read().decode(errors="replace"))
        except Exception as e:  # noqa: BLE001
            return ApiResult(0, f"{type(e).__name__}: {e}")

    return CheckCtx(repo=repo, sh=sh, api=api)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - live only
    import argparse

    ap = argparse.ArgumentParser(description="Compounding scorecard — grade one night + report the trajectory.")
    ap.add_argument("--repo", required=True, help="path to the checked-out target repo at HEAD")
    ap.add_argument("--store", required=True, help="JSONL run log to append to / read")
    ap.add_argument("--night", type=int, required=True, help="1-based night index")
    ap.add_argument("--backend-url", required=True, help="base URL of the already-booted backend")
    ap.add_argument("--repo-ref", default="HEAD", help="the graded HEAD sha (for the record)")
    ap.add_argument("--tokens", type=int, default=None, help="tokens the goal spent this night (idle detection)")
    args = ap.parse_args(argv)

    ctx = _live_ctx(args.repo, args.backend_url)
    rec = run_night(ctx, night=args.night, repo_ref=args.repo_ref, tokens=args.tokens)
    append_night(args.store, rec)
    print(render_report(analyze(load_history(args.store)), load_history(args.store)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
