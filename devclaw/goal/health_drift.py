"""Instance health drift detection — periodic, zero-LLM environmental probe.

Four probes (issue #596):
  - disk_headroom: ``shutil.disk_usage`` on the goals-dir filesystem (the volume
    backing workspaces); a percentage.
  - docker_disk_headroom: ``shutil.disk_usage`` on docker's data-root directory
    (resolved via ``docker info``); recorded separately because the docker volume
    may live on a different filesystem from workspaces.
  - orphan_docker_volumes: ``docker volume ls`` filtered to the
    ``devclaw-toolchains-*`` prefix; count of volumes not accounted for by any
    registered project workspace.
  - stale_workspaces: count of workspace directories sweep-eligible right now
    (terminal goal, past retention, still on disk) via ``host_resources.sweep_candidates``.

Every probe is individually wrapped — a failure degrades to ``None`` (unknown):
no problem recorded, no exception raised. Zero LLM on every path.

The scheduled edge in ``goal/service.py`` gates on a ``health_drift_last_check_ms``
meta key so probes run at most once per ``DEVCLAW_HEALTH_INTERVAL_S`` (default
3600 s), keeping the docker subprocess off the idle-tick hot path.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from ..state_store import StateStore

#: Meta key storing the epoch-ms of the last successful health check run.
_LAST_CHECK_META = "health_drift_last_check_ms"

#: Docker volume name prefix for all devclaw-owned toolchain caches.
_TOOLCHAIN_VOLUME_PREFIX = "devclaw-toolchains-"

#: Timeout for the docker CLI call, mirroring the release-path seam in host_resources.
_DOCKER_TIMEOUT_S = 20


# ---- individual probes (each returns a value or None for unknown) ----------


def _docker_root_dir(docker_bin: str) -> Optional[str]:
    """Docker data-root directory via ``docker info``, or ``None`` on any failure."""
    try:
        result = subprocess.run(
            [docker_bin, "info", "--format", "{{.DockerRootDir}}"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
        )
        if result.returncode != 0:
            return None
        path = result.stdout.strip()
        return path if path else None
    except Exception:  # noqa: BLE001 — degrade silently, never raise
        return None


def _docker_disk_used_pct(docker_bin: str) -> Optional[float]:
    """Used % of docker's data-root filesystem, or ``None`` on any failure.

    Resolves the data-root via ``docker info`` then delegates to ``_disk_used_pct``.
    A missing daemon, timeout, or empty path all degrade to ``None``.
    """
    root = _docker_root_dir(docker_bin)
    if root is None:
        return None
    return _disk_used_pct(root)


def _disk_used_pct(path: str) -> Optional[float]:
    """Filesystem used-% for the path's volume, or ``None`` on any failure."""
    try:
        u = shutil.disk_usage(path)
        return 100.0 * u.used / u.total if u.total else None
    except Exception:  # noqa: BLE001 — degrade silently, never raise
        return None


def _orphan_docker_volume_count(
    docker_bin: str,
    project_workspaces: "set[str]",
) -> Optional[int]:
    """Count docker toolchain volumes not accounted for by any registered project.

    Returns ``None`` when the docker probe fails (daemon unavailable, binary
    missing, timeout). Expected volume names are derived from the registered
    project workspaces using the same ``_toolchain_volume_name`` function the
    engine uses at mount time — the translation step (container path → host path)
    is load-bearing (see ``host_resources.toolchain_volume_for``).
    """
    try:
        from ..engine.sandcastle import _toolchain_volume_name, _translate_workspace_path

        expected = {
            _toolchain_volume_name(_translate_workspace_path(ws))
            for ws in project_workspaces
        }
        result = subprocess.run(
            [
                docker_bin, "volume", "ls",
                "--filter", f"name={_TOOLCHAIN_VOLUME_PREFIX}",
                "--format", "{{.Name}}",
            ],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
        )
        if result.returncode != 0:
            return None
        live = {ln.strip() for ln in result.stdout.splitlines() if ln.strip()}
        return sum(1 for v in live if v not in expected)
    except Exception:  # noqa: BLE001 — degrade silently
        return None


def _stale_workspace_count(
    goals: Iterable[Any],
    project_workspaces: "set[str]",
    now_ms: int,
) -> Optional[int]:
    """Count workspace directories that are sweep-eligible right now.

    Sweep-eligible = all goals on that workspace are terminal + past retention.
    Delegates to ``host_resources.sweep_candidates`` for consistency with the
    actual sweep logic; the full candidate list (no batch cap) gives the raw count.
    Returns ``None`` on any failure.
    """
    try:
        from ..host_resources import sweep_candidates

        return len(
            sweep_candidates(
                goals=list(goals),
                project_workspaces=project_workspaces,
                now_ms=now_ms,
            )
        )
    except Exception:  # noqa: BLE001 — degrade silently
        return None


# ---- orchestrator ----------------------------------------------------------


def run_health_drift_checks(
    *,
    store: "StateStore",
    goals: Iterable[Any],
    project_workspaces: "set[str]",
    now_ms: int,
    goals_dir: str,
    disk_warn_pct: float,
    docker_disk_warn_pct: float,
    orphan_docker_warn: int,
    stale_ws_warn: int,
    docker_bin: str,
) -> None:
    """Run all four probes and record problems for breached thresholds.

    Never raises. A probe returning ``None`` (unknown) produces no record —
    unknown is not an alarm and is not a false all-clear.
    """
    _check_disk(store, goals_dir, disk_warn_pct)
    _check_docker_disk(store, docker_bin, docker_disk_warn_pct)
    goals_list = list(goals)
    _check_orphan_volumes(store, docker_bin, project_workspaces, orphan_docker_warn)
    _check_stale_workspaces(store, goals_list, project_workspaces, now_ms, stale_ws_warn)


def _check_docker_disk(
    store: "StateStore", docker_bin: str, threshold_pct: float
) -> None:
    pct = _docker_disk_used_pct(docker_bin)
    if pct is None:
        return
    if pct >= threshold_pct:
        store.record_problem(
            category="other",
            kind="docker_root_disk_high",
            message=(
                f"docker data-root filesystem at {pct:.0f}% capacity "
                f"(threshold: {threshold_pct:.0f}%)"
            ),
            recovered=False,
        )


def _check_disk(store: "StateStore", goals_dir: str, threshold_pct: float) -> None:
    pct = _disk_used_pct(goals_dir)
    if pct is None:
        return
    if pct >= threshold_pct:
        store.record_problem(
            category="other",
            kind="disk_usage_high",
            message=(
                f"workspace filesystem at {pct:.0f}% capacity "
                f"(threshold: {threshold_pct:.0f}%)"
            ),
            recovered=False,
        )


def _check_orphan_volumes(
    store: "StateStore",
    docker_bin: str,
    project_workspaces: "set[str]",
    threshold: int,
) -> None:
    count = _orphan_docker_volume_count(docker_bin, project_workspaces)
    if count is None:
        return
    if count >= threshold:
        store.record_problem(
            category="other",
            kind="orphan_docker_volumes",
            message=(
                f"{count} docker toolchain volume(s) have no registered project "
                f"(threshold: {threshold})"
            ),
            recovered=False,
        )


def _check_stale_workspaces(
    store: "StateStore",
    goals: list[Any],
    project_workspaces: "set[str]",
    now_ms: int,
    threshold: int,
) -> None:
    count = _stale_workspace_count(goals, project_workspaces, now_ms)
    if count is None:
        return
    if count >= threshold:
        store.record_problem(
            category="other",
            kind="stale_workspaces",
            message=(
                f"{count} workspace directory(ies) are sweep-eligible but not yet "
                f"swept (threshold: {threshold})"
            ),
            recovered=False,
        )
