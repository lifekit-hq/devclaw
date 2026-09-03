"""Is a delivered PR still cleanly mergeable? — one read-only question for GitHub.

This module used to be ``merge.py`` and used to MERGE. It doesn't any more:
auto-merge, the per-action delivery topology it keyed off, and the program
PR-stack reconciler were deleted in #641. The short version of that decision,
because a future reader will otherwise reinvent them:

* Every goal delivers on a shared ``goal/<id>`` branch as ONE cumulative PR, and
  that PR must stay OPEN for the done-gate (#486). Merging it mid-flight deletes
  the branch and forces the next run to re-fork from main, which is how a goal
  loses its accumulated work.
* The reconciler existed to shepherd a STACK of per-action PRs to main and close
  the superseded ones. Goal-branch delivery never makes a stack — delivery is
  still one push, one PR.
* In companion mode a human reviews and merges. Machinery that merges without one
  is compensating for an absent reviewer, not adding a capability.

What survives is the advisory: a delivered PR that has gone CONFLICTING with its
base is worth saying out loud, because the next increment will stack on top of a
branch that can no longer land. Saying it is all this does.

Spec 025 (2026-08-29) later reversed the merge doctrine at EXACTLY ONE seam —
the confirmed-achieved close squash-merges the cumulative PR
(``merge_on_close.py``, wired in ``tick_donegate``). That reversal does not
touch this module or the settle path: nothing merges mid-flight, #486 stands,
and this probe stays read-only.

The gh call lives here rather than in ``tick_settle`` so the tick stays a pure,
subprocess-free unit under test; ``GoalService`` binds the real probe and tests
inject a fake.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

#: takes a PR url, returns True iff GitHub reports it CONFLICTING with its
#: base, False iff it is cleanly mergeable, None when it cannot tell (gh
#: missing/erroring, or GitHub still computing mergeability). None means
#: "say nothing", never "all clear" — the settle path only speaks on a
#: definite CONFLICTING.
MergeabilityProbe = Callable[[str], Awaitable[Optional[bool]]]


#: Wall-clock bound on every host-side ``gh`` read that runs inside the tick.
#: None of these helpers had a timeout before spec 032: a hung ``gh`` (GitHub
#: outage, a stuck auth prompt) blocked the tick loop's event loop for every
#: goal. A timeout is reported like a spawn failure — ``(-1, msg)`` — so each
#: caller's "say nothing / unknown" posture applies unchanged.
GH_TIMEOUT_S = 20.0


async def run_bounded(
    *argv: str, cwd: "str | None" = None, timeout_s: "float | None" = None
) -> tuple[int, str]:
    """Run a subprocess under a wall-clock bound, returning
    ``(returncode, combined stdout+stderr)``. Best-effort in exactly one
    direction: a spawn failure OR a timeout returns ``(-1, "<reason>")`` and
    never raises into the tick. On timeout the child is killed so nothing
    lingers past the bound."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never break the tick
        return -1, f"{exc.__class__.__name__}: {exc}"
    bound = GH_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=bound)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return -1, f"timeout after {bound:g}s: {' '.join(argv[:3])}"
    except Exception as exc:  # noqa: BLE001 — best-effort; never break the tick
        return -1, f"{exc.__class__.__name__}: {exc}"
    rc = proc.returncode
    assert rc is not None  # communicate() returned, so the process exited
    return rc, out.decode(errors="replace").strip()


async def _run_gh(*argv: str) -> tuple[int, str]:
    """Run a subprocess, returning ``(returncode, combined stdout+stderr)``.
    Best-effort: a spawn failure or a timeout returns ``(-1, msg)`` and never
    raises into the tick (see :func:`run_bounded`)."""
    return await run_bounded(*argv)


async def pr_conflicting(pr_url: str) -> Optional[bool]:
    """The production :data:`MergeabilityProbe`: one ``gh pr view`` asking
    GitHub whether the PR is CONFLICTING with its base. Best-effort and
    never raises — any failure (no gh, network, an unparseable answer, or
    GitHub's transient ``UNKNOWN`` while it recomputes mergeability) returns
    None, and the caller stays silent rather than guessing."""
    if not pr_url:
        return None
    rc, out = await _run_gh(
        "gh", "pr", "view", pr_url, "--json", "mergeable", "-q", ".mergeable"
    )
    if rc != 0:
        return None
    verdict = out.strip().upper()
    if verdict == "CONFLICTING":
        return True
    if verdict == "MERGEABLE":
        return False
    return None
