"""Durable host resources devclaw creates — and the release path that retires them.

devclaw creates two kinds of thing on the host that outlive the task that made
them, and until now nothing ever removed either:

* the **project workspace** — the checkout at ``project.workspace_dir``;
* the **per-project toolchain volume** — the named docker volume
  ``engine/sandcastle.py`` mounts to cache mise-provisioned toolchains.

The leak they caused was never an ownership problem. Since spec 003 every
dispatch entry point resolves a ``project_id`` into the PROJECT's workspace
(``server/tools/_common.py::_resolve_project_or_reject``), so the workspace is a project
resource recorded in the registry, and the volume name is a pure function of
that path. What was missing is the other half of ownership: a *release*.
``delete_project`` dropped the registry row and left ~1G of ledger checkout on
disk with nothing that could ever find it again.

**Ownership over inference.** Everything here is derived from a record devclaw
already holds. Nothing scans a filesystem for things that "look abandoned" and
nothing infers that an unrecognised directory is garbage — the failure mode of
inference is that a docker probe hiccup returns "no live goals" and the whole
cache set looks orphaned. The rule this module encodes:

    Authority to delete comes from a record. Visibility comes from a scan.
    Never swap them.

So this module deletes ONLY what a record names, and it is deliberately blind to
anything else on disk. Making that blindness visible — reporting the divergence
between what is recorded and what is present — is a separate, read-only job
(issue #596) that must never be allowed to delete anything.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config as _config
from .engine.sandcastle import (
    DOCKER_BIN,
    _toolchain_volume_name,
    _translate_workspace_path,
)
from .project_registry import _normalize_workspace

#: Goal phases with no outgoing events (``goal/transitions.py``). A goal in any
#: OTHER phase — including ``blocked``, which is resumable — owns live state and
#: its resources are never released.
TERMINAL_PHASES = frozenset({"done", "cancelled"})

#: Bound on the docker CLI call, mirroring the sandbox sweep's seam.
_DOCKER_TIMEOUT_S = 20


def toolchain_volume_for(workspace_dir: str) -> str:
    """The toolchain volume name for a workspace, derived exactly as
    ``sandcastle`` derives it when it mounts one.

    The translation step is load-bearing and easy to get wrong: sandcastle names
    the volume from the HOST view of the bind path, not the path devclaw sees
    inside its own container. On the live VPS
    ``/var/lib/devclaw/workspaces/devclaw`` (container) and
    ``/srv/devclaw/workspaces/devclaw`` (host) hash to different names, and only
    the host one exists. Skipping the translation would silently look for a
    volume that never existed and report "nothing to release" forever.
    """
    return _toolchain_volume_name(_translate_workspace_path(workspace_dir))


def _same_workspace(a: "str | None", b: "str | None") -> bool:
    """Workspace identity, on the registry's normalized-path axis."""
    na, nb = _normalize_workspace(a), _normalize_workspace(b)
    return na is not None and na == nb


def release_blockers(
    workspace_dir: str,
    *,
    goals: Iterable[Any],
    running_tasks: Iterable[Any] = (),
) -> list[str]:
    """Why this workspace's resources must NOT be released right now, or ``[]``.

    Two blocking conditions, both about live state rather than age:

    * a goal on this workspace is in a non-terminal phase — it is still working
      or is blocked and resumable, and its checkout is live state;
    * a task on this workspace is running — the sandbox has it bind-mounted.

    Both can be true at once (a goal cancelled while its task is still in
    flight), which is exactly why the task check is separate from the phase
    check rather than derived from it.
    """
    blockers: list[str] = []
    for goal in goals:
        phase = _attr(goal, "phase")
        if not _same_workspace(_attr(goal, "workspace_dir"), workspace_dir):
            continue
        if phase not in TERMINAL_PHASES:
            blockers.append(
                f"goal {_attr(goal, 'id')!r} is in phase {phase!r} (not terminal)"
            )
    for task in running_tasks:
        if _same_workspace(_attr(task, "workspace_dir"), workspace_dir):
            blockers.append(f"task {_attr(task, 'id')!r} is still running")
    return blockers


def _attr(obj: Any, name: str) -> Any:
    """Read a field off either a dataclass-ish record or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _docker_run_sync(args: list[str]) -> "subprocess.CompletedProcess[str]":
    """One bounded docker CLI call — the subprocess seam tests patch (mirrors
    ``sandcastle.sweep_orphan_sandboxes``)."""
    return subprocess.run(
        [DOCKER_BIN, *args],
        capture_output=True,
        text=True,
        timeout=_DOCKER_TIMEOUT_S,
    )


def _unsafe_workspace_path(workspace_dir: str) -> "str | None":
    """Reject a path that must never be handed to ``rmtree``, or ``None``.

    Deleting a directory is irreversible on the host — there is no trash and no
    undo — so a malformed record must fail loudly here rather than take out a
    mount root. This is a guard against a bad *record*, not against a caller.
    """
    if not workspace_dir or not workspace_dir.strip():
        return "workspace_dir is empty"
    path = Path(workspace_dir)
    if not path.is_absolute():
        return f"workspace_dir {workspace_dir!r} is not an absolute path"
    if path.parent == path:
        return f"refusing to remove filesystem root {workspace_dir!r}"
    if len(path.parts) < 3:
        return (
            f"refusing to remove {workspace_dir!r} — too close to the root to be "
            f"a workspace"
        )
    return None


def release_project_resources(
    workspace_dir: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Release the durable host resources owned by ``workspace_dir``.

    Removes the workspace directory and the project's toolchain volume. Callers
    are responsible for checking :func:`release_blockers` first — this function
    does what it is told, so that the "is it safe" decision lives in ONE place
    at the call site rather than being re-litigated here.

    Best-effort per resource and never raises: a resource that cannot be removed
    is reported in ``failed`` with its reason (issue #595 — a failure is
    surfaced, not silently retried forever). ``dry_run`` reports what would be
    removed and touches nothing.

    Returns ``{"released": [...], "failed": [...], "dry_run": bool}`` where each
    entry names the resource kind and its identity, so an operator can answer
    "what deleted my directory, and why" after the fact.
    """
    released: list[dict] = []
    failed: list[dict] = []

    volume = toolchain_volume_for(workspace_dir)

    unsafe = _unsafe_workspace_path(workspace_dir)
    if unsafe:
        failed.append({"kind": "workspace", "id": workspace_dir, "reason": unsafe})
    elif not Path(workspace_dir).exists():
        # Already gone (hand-deleted, or never created). Not an error — a record
        # claiming a resource that no longer exists is drift, not a failure.
        pass
    elif dry_run:
        released.append({"kind": "workspace", "id": workspace_dir})
    else:
        try:
            shutil.rmtree(workspace_dir)
            released.append({"kind": "workspace", "id": workspace_dir})
        except OSError as exc:
            failed.append(
                {"kind": "workspace", "id": workspace_dir, "reason": str(exc)}
            )
            sys.stderr.write(
                f"host-resources: could not remove workspace {workspace_dir}: {exc}\n"
            )

    if dry_run:
        released.append({"kind": "toolchain_volume", "id": volume})
    else:
        try:
            rm = _docker_run_sync(["volume", "rm", volume])
        except (OSError, subprocess.SubprocessError) as exc:
            # docker missing/unreachable (host + stub engines, CI): nothing to
            # release here. Not a failure — this environment never made one.
            sys.stderr.write(
                f"host-resources: docker unavailable, volume {volume} not "
                f"released: {exc}\n"
            )
        else:
            if rm.returncode == 0:
                released.append({"kind": "toolchain_volume", "id": volume})
            elif "no such volume" in (rm.stderr or "").lower():
                pass  # drift, same as an already-absent directory
            else:
                reason = (rm.stderr or "").strip() or f"exit {rm.returncode}"
                failed.append(
                    {"kind": "toolchain_volume", "id": volume, "reason": reason}
                )

    return {"released": released, "failed": failed, "dry_run": dry_run}


def release_for_project(
    workspace_dir: Optional[str],
    *,
    goals: Iterable[Any],
    running_tasks: Iterable[Any] = (),
    dry_run: bool = False,
) -> dict:
    """Check-then-release: the whole path in one call for a tool call site.

    Returns the :func:`release_project_resources` shape, plus ``blocked`` — a
    non-empty list means nothing was touched and says why.
    """
    if not workspace_dir:
        return {"released": [], "failed": [], "blocked": [], "dry_run": dry_run}
    blockers = release_blockers(
        workspace_dir, goals=goals, running_tasks=running_tasks
    )
    if blockers:
        return {
            "released": [],
            "failed": [],
            "blocked": blockers,
            "dry_run": dry_run,
        }
    out = release_project_resources(workspace_dir, dry_run=dry_run)
    out["blocked"] = []
    return out


# --- retention sweep: workspaces of goals that ended ----------------------
#
# ``delete_project`` above releases what a PROJECT owns, on an explicit human
# verb. This sweep is the timer half: a goal-scoped workspace whose goal ended
# long enough ago that nobody is going to look at it again.
#
# It follows the trace/events retention pattern exactly (``StateStore.
# _maybe_prune_table``): a cycle gated by a persisted watermark, a bounded batch
# per tick, and the watermark advanced only when a batch comes back short, so a
# 34-directory backlog drains across ticks instead of wedging one. Zero LLM
# calls on every path — it rides the heartbeat's cheap maintenance slot, after
# the phase gates.

#: Days to keep a cleanly-finished goal's workspace. Short: a goal that reached
#: its done-gate has its work on a branch, and nobody inspects the checkout.
WORKSPACE_RETENTION_DAYS_DEFAULT = 3

#: Days to keep the workspace of a goal that ended BADLY. Long: this is the case
#: where the checkout is the evidence — what the worker actually left behind
#: when it went off track — and it is the only forensics that exists.
WORKSPACE_RETENTION_DAYS_FAILED_DEFAULT = 14

#: One sweep cycle per day, like the trace prune.
_SWEEP_INTERVAL_MS = 24 * 3600 * 1000

#: Directories removed per tick. Deliberately small — each is an irreversible
#: rmtree of up to a few hundred MB, and there is never a hurry.
SWEEP_BATCH = 5

#: Directions that mean the goal ended badly. ``blocked_on`` set at a terminal
#: phase means the same thing.
_BAD_DIRECTIONS = frozenset({"off_track", "stalled"})


def _parse_days(raw: "str | None", default: int) -> int:
    """Retention in days from a raw env value, ``<= 0`` disables — the same
    contract (and the same shape) as ``state_store.core._parse_retention_days``.
    The env NAME stays a literal at the call site so the doc-parity scanner in
    ``tests/test_env_vars_doc_sync.py`` can see the read."""
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def workspace_retention_days() -> int:
    """Clean-terminal workspace retention from ``DEVCLAW_WORKSPACE_RETENTION_DAYS``."""
    return _parse_days(
        _config.workspace_retention_days_raw(),
        WORKSPACE_RETENTION_DAYS_DEFAULT,
    )


def failed_workspace_retention_days() -> int:
    """Forensic retention from ``DEVCLAW_WORKSPACE_RETENTION_DAYS_FAILED``."""
    return _parse_days(
        _config.failed_workspace_retention_days_raw(),
        WORKSPACE_RETENTION_DAYS_FAILED_DEFAULT,
    )


def goal_ended_badly(goal: Any) -> bool:
    """Whether a terminal goal is one an operator might still want to inspect.

    devclaw has no ``failed`` phase — a goal ends ``done`` or ``cancelled``. The
    distinction the retention windows care about is not the phase but whether it
    ended in a state worth looking at: an off-track/stalled direction, or a
    block that was never cleared.
    """
    if (_attr(goal, "direction") or "") in _BAD_DIRECTIONS:
        return True
    return bool((_attr(goal, "blocked_on") or "").strip())


def _goal_quiet_since_ms(goal: Any) -> "int | None":
    """When this goal last did anything, in epoch ms, or ``None`` if unknown.

    ``None`` means the age is unknown, and an unknown age is never old enough —
    a goal with no timestamps is left alone rather than swept on a guess.
    """
    from datetime import datetime

    best: "int | None" = None
    for field in ("last_progress_at", "last_tick_at", "last_eval_at", "last_plan_at"):
        raw = _attr(goal, field)
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        ms = int(stamp.timestamp() * 1000)
        best = ms if best is None else max(best, ms)
    return best


def sweep_candidates(
    *,
    goals: Iterable[Any],
    project_workspaces: "set[str]",
    now_ms: int,
    retention_days: "int | None" = None,
    failed_retention_days: "int | None" = None,
) -> list[dict]:
    """Goal-scoped workspaces old enough to release, newest exclusions first.

    A workspace is a candidate only when ALL of these hold:

    * every goal on it is terminal (live state is never swept — enforced again
      at release time by :func:`release_blockers`);
    * it is NOT the workspace of a registered project. A project owns its
      checkout and releases it through ``delete_project``; sweeping it because
      its goals happen to be terminal would delete a live project's clone and
      force a re-clone on its next goal.
    * the newest goal on it went quiet longer ago than its retention window.

    Returns dicts of ``{workspace_dir, goal_ids, bad, age_days}``.
    """
    keep = retention_days if retention_days is not None else workspace_retention_days()
    keep_bad = (
        failed_retention_days
        if failed_retention_days is not None
        else failed_workspace_retention_days()
    )
    if keep <= 0 and keep_bad <= 0:
        return []

    by_ws: dict[str, list[Any]] = {}
    for goal in goals:
        ws = _normalize_workspace(_attr(goal, "workspace_dir"))
        if ws is None:
            continue
        by_ws.setdefault(ws, []).append(goal)

    out: list[dict] = []
    for ws, ws_goals in by_ws.items():
        if ws in project_workspaces:
            continue  # a registered project owns this — delete_project's job
        if any(_attr(g, "phase") not in TERMINAL_PHASES for g in ws_goals):
            continue
        quiet = [_goal_quiet_since_ms(g) for g in ws_goals]
        if any(q is None for q in quiet):
            continue  # unknown age is never old enough
        newest = max(q for q in quiet if q is not None)
        bad = any(goal_ended_badly(g) for g in ws_goals)
        window_days = keep_bad if bad else keep
        if window_days <= 0:
            continue
        age_ms = now_ms - newest
        if age_ms < window_days * 24 * 3600 * 1000:
            continue
        out.append(
            {
                "workspace_dir": _attr(ws_goals[0], "workspace_dir"),
                "goal_ids": sorted(str(_attr(g, "id")) for g in ws_goals),
                "bad": bad,
                "age_days": round(age_ms / (24 * 3600 * 1000), 1),
            }
        )
    # Oldest first — drain the longest-dead directories before recent ones.
    out.sort(key=lambda c: -c["age_days"])
    return out


def sweep_terminal_goal_workspaces(
    *,
    goals: Iterable[Any],
    running_tasks: Iterable[Any] = (),
    project_workspaces: "set[str] | None" = None,
    now_ms: int,
    batch_limit: int = SWEEP_BATCH,
    retention_days: "int | None" = None,
    failed_retention_days: "int | None" = None,
    dry_run: bool = False,
) -> dict:
    """One bounded batch of the workspace retention sweep.

    ``project_workspaces=None`` means the caller could not determine which
    workspaces belong to registered projects, and the sweep does NOTHING rather
    than risk deleting a live project's checkout. Absence of information is
    never treated as permission.

    Returns ``{"released": [...], "failed": [...], "considered": n,
    "drained": bool}``; ``drained`` is True when this batch came back short,
    which is the caller's signal to stamp the daily watermark.
    """
    if project_workspaces is None:
        return {"released": [], "failed": [], "considered": 0, "drained": True}

    candidates = sweep_candidates(
        goals=goals,
        project_workspaces=project_workspaces,
        now_ms=now_ms,
        retention_days=retention_days,
        failed_retention_days=failed_retention_days,
    )
    released: list[dict] = []
    failed: list[dict] = []
    acted = 0
    goals_list = list(goals)
    tasks_list = list(running_tasks)

    for cand in candidates:
        if acted >= batch_limit:
            break
        acted += 1
        out = release_for_project(
            cand["workspace_dir"],
            goals=goals_list,
            running_tasks=tasks_list,
            dry_run=dry_run,
        )
        if out["blocked"]:
            failed.append(
                {
                    "kind": "workspace",
                    "id": cand["workspace_dir"],
                    "reason": "; ".join(out["blocked"]),
                }
            )
            continue
        for entry in out["released"]:
            released.append({**entry, "age_days": cand["age_days"], "bad": cand["bad"]})
        failed.extend(out["failed"])

    return {
        "released": released,
        "failed": failed,
        "considered": len(candidates),
        "drained": acted < batch_limit,
    }
