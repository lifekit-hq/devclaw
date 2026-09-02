"""The ONE seam that raises a typed Problem (spec 031 US1).

A goal that cannot proceed for a human-gated reason states its problem as a
typed thing — what is wrong, which ``done_when`` clause, why the loop cannot
decide it, two to five bounded options, a default, a timebox — instead of
prose in ``blocked_on``. Four sites raise today (the done-gate's
``needs_human``, the churn park, a worker honest-block, the dispatch-time
park); all go through :func:`raise_problem` so the shape cannot fork
(constitution VII).

Pure mechanism: no cognition here, ever. Options come from the evaluator's
own ``corrections`` when it produced some, else from the fixed sets below —
and a Problem says which (loud, constitution VI). ``raise_problem`` writes
rows only; the caller performs the BLOCK transition in the SAME transaction
and carries ``problem_id`` + :func:`summary_line` into the status row, so a
``TransitionConflict`` rolls the Problem back with the block.
"""

from __future__ import annotations

import time
import uuid

from .. import config as _config
from .models import Problem, ProblemOption

#: fixed tails / sets. Keys are stable — the console, the ping and the verbs
#: all key off them; labels are the owner-facing text.
ACCEPT_CLOSE = ProblemOption(
    "accept_close", "Accept the gap and close",
    "the clause is recorded as resolved by your decision; the done-gate closes the goal on its next round",
    closes_goal=True,
)
SPLIT = ProblemOption(
    "split", "Split into a follow-up",
    "the clause is recorded as deferred; the goal closes without it and a follow-up is noted on the close",
)
CORRECT = ProblemOption(
    "correct", "Correct the implementation",
    "re-dispatch with the correction recorded against the clause as settled fact",
)
SUPPLY = ProblemOption(
    "supply", "Supply the capability",
    "provide what the sandbox lacks (credential, service, access), then resume",
)
CANCEL = ProblemOption(
    "cancel", "Cancel the goal",
    "the goal is cancelled; nothing merges",
)

CHURN_OPTIONS: tuple[ProblemOption, ...] = (CORRECT, ACCEPT_CLOSE, SPLIT)
WORKER_BLOCK_OPTIONS: tuple[ProblemOption, ...] = (CORRECT, SUPPLY, CANCEL)
FALLBACK_NOTE = " (the loop could not derive options from the verdict; the fixed set is offered)"

_MAX_OPTIONS = 5


def options_from_corrections(corrections: "list[str] | tuple[str, ...]") -> tuple[ProblemOption, ...]:
    """Promote the evaluator's corrections to options (``c1``…``cN``), then the
    fixed tail. Bounded to five; empty corrections ⇒ the churn set."""
    opts: list[ProblemOption] = []
    for i, text in enumerate(c for c in corrections if isinstance(c, str) and c.strip()):
        if len(opts) >= _MAX_OPTIONS - 2:
            break
        t = " ".join(text.split())
        opts.append(ProblemOption(f"c{i + 1}", t[:160], "re-dispatch with this correction recorded as settled fact"))
    if not opts:
        return CHURN_OPTIONS
    return tuple(opts) + (ACCEPT_CLOSE, SPLIT)


def new_problem(
    goal_id: str, *, kind: str, raised_by: str, what: str, clause: str, why: str,
    options: "tuple[ProblemOption, ...]", default_key: "str | None" = None,
    timebox_s: "int | None" = None, now_ms: "int | None" = None,
) -> Problem:
    """Build (do not persist) a Problem with the spec's invariants applied:
    2–5 options, a valid default, a timebox strictly after ``raised_at``."""
    opts = tuple(options)[:_MAX_OPTIONS]
    if len(opts) < 2:
        opts = CHURN_OPTIONS
        what = what + FALLBACK_NOTE
    keys = {o.key for o in opts}
    default = default_key if default_key in keys else opts[0].key
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    tb = _config.problem_timebox_s() if timebox_s is None else timebox_s
    # 0 disables defaulting: park the timebox far enough that no tick reaches it
    timebox_at = now + (tb * 1000 if tb > 0 else 10 * 365 * 24 * 3600 * 1000)
    return Problem(
        id=f"prb_{uuid.uuid4().hex[:20]}", goal_id=goal_id, kind=kind,
        raised_by=raised_by, what=" ".join((what or "").split())[:2000],
        clause=(clause or "").strip()[:400], why=" ".join((why or "").split())[:1000],
        options=opts, default_key=default, timebox_at=timebox_at,
        status="open", raised_at=now,
    )


def raise_problem(store, problem: Problem) -> Problem:
    """Persist ``problem`` as the goal's one OPEN Problem. Call inside the
    same ``store.transaction()`` as the BLOCK transition, and set
    ``problem_id=problem.id`` and ``blocked_on=summary_line(problem)`` on the
    status you transition to."""
    return store.raise_problem(problem)


def summary_line(p: Problem) -> str:
    """The one-line ``blocked_on`` for readers that predate spec 031."""
    where = f' on "{p.clause}"' if p.clause else ""
    return f"{p.kind}{where}: {p.what[:160]} — see problem {p.id}"


def render_for_human(p: Problem) -> str:
    """The Problem as the owner reads it — ping, refusal message, console
    fallback. Names the two verbs; never names steer_goal."""
    lines = [f"problem {p.id} — {p.clause or 'contract'}", p.what]
    if p.why:
        lines.append(f"why the loop cannot decide it: {p.why}")
    for i, o in enumerate(p.options, 1):
        mark = " ← default" if o.key == p.default_key else ""
        lines.append(f"({i}) [{o.key}] {o.label}{mark}")
    remaining = max(0, p.timebox_at - int(time.time() * 1000)) // 1000
    if remaining:
        h, m = divmod(remaining // 60, 60)
        lines.append(f"default applies in {h}h{m:02d}m. Resolve with correct_implementation or decide.")
    else:
        lines.append("Resolve with correct_implementation or decide.")
    return "\n".join(lines)


def to_dict(p: Problem) -> dict:
    """The MCP / HTTP read shape (contracts/mcp-and-http.md)."""
    return {
        "id": p.id, "kind": p.kind, "raised_by": p.raised_by, "what": p.what,
        "clause": p.clause, "why": p.why,
        "options": [
            {"key": o.key, "label": o.label, "consequence": o.consequence, "closes_goal": o.closes_goal}
            for o in p.options
        ],
        "default": p.default_key, "timebox_at": p.timebox_at,
        "raised_at": p.raised_at, "status": p.status,
    }
