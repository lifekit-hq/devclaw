"""Durable host resources devclaw creates — and the release path that retires them.

devclaw creates two kinds of thing on the host that outlive the task that made
them, and until now nothing ever removed either:

* the **project workspace** — the checkout at ``project.workspace_dir``;
* the **per-project toolchain volume** — the named docker volume
  ``engine/sandcastle.py`` mounts to cache mise-provisioned toolchains.

The leak they caused was never an ownership problem. Since spec 003 every
dispatch entry point resolves a ``project_id`` into the PROJECT's workspace
(``server/tools.py::_resolve_project_or_reject``), so the workspace is a project
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
