"""Admission — the dispatch-admission brakes of the queue, split out as a mixin.

:class:`AdmissionMixin` carries the two launch gates consulted by ``_pump``:
the host-memory admission budget (the `claude --print` -9 OOM cure) and the
per-workspace failure circuit-breaker. Their tuning constants
(``MEM_LAUNCH_FLOOR_BYTES``, ``WORKSPACE_BREAK_*``) are bound HERE — tests
that tune the breaker patch THIS namespace. ``host_mem_available_bytes``
itself stays bound in ``task_queue`` (its reader ``_pump`` lives there).

Split out of ``TaskQueue`` as a mixin on the SAME instance — every method here
runs against the ``self._store`` / ``self._mem_budget`` the base ``TaskQueue``
owns, so the fail-open-on-unmeasurable / one-shot-per-hold semantics are
byte-identical to the pre-split monolith. This module must never import
``devclaw.task_queue`` at runtime (the dependency points the other way).
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from .. import config as _config
from ..engine.sandcastle import SANDBOX_MEMORY
from ..host_resources import _parse_mem
from ..state_store import _now_ms

if TYPE_CHECKING:
    from ..state_store import StateStore

# ---- host-memory admission (the `claude --print` -9 OOM cure) ----------------
# A COUNT cap (GLOBAL_MAX_CONCURRENT) plus a per-container `--memory` CEILING is
# NOT the same as fitting the host: docker will start N containers each ALLOWED
# 2g even when the box lacks N*2g (a --memory limit is a ceiling, not a
# reservation). Under load the kernel's global OOM-killer then reaps the fattest
# unbounded process — the host-side `claude --print` cognition running in the
# root cgroup — as `exited -9`. #448/#449 classified that signal-death TRANSIENT
# and retried it: survivable, but it never asked why the kernel was reaping us.
# This gates dispatch on REAL free RAM so we never overcommit in the first place.
# Mechanism, not appeasement (doctrine: fix the repo, don't appease it).


#: per-sandbox memory ceiling in bytes — the SAME value the launcher hands to
#: ``docker --memory`` (imported, so the two can't drift).
SANDBOX_MEMORY_BYTES = _parse_mem(SANDBOX_MEMORY)
#: headroom kept free for the host ``claude --print`` cognition + OS so it is not
#: the OOM victim. Env-tunable per host (``DEVCLAW_COGNITION_MEM_RESERVE``); the
#: floor to admit one more sandbox launch is sandbox-ceiling + this reserve.
COGNITION_MEM_RESERVE_BYTES = _parse_mem(
    _config.COGNITION_MEM_RESERVE
)
MEM_LAUNCH_FLOOR_BYTES = SANDBOX_MEMORY_BYTES + COGNITION_MEM_RESERVE_BYTES

#: per-workspace circuit-breaker: N task failures on the same workspace_dir
#: within WINDOW_S trips a hold for HOLD_S. Sibling of the global quota pause but
#: scoped, so one broken workspace doesn't starve the others. Trigger event that
#: named this: 2026-07-02 closeloop retry storm — 6+ duplicate dispatches racing
#: on the same repo burned quota with zero PR output because per-task retries
#: alone don't stop a workspace-level defect. Threshold <=0 disables.
WORKSPACE_BREAK_THRESHOLD = 3
WORKSPACE_BREAK_WINDOW_S = 900.0
WORKSPACE_BREAK_HOLD_S = 1800.0


class AdmissionMixin:
    if TYPE_CHECKING:
        # The composing class (TaskQueue) owns these; declared under
        # TYPE_CHECKING so the seam is checked, never run.
        _store: StateStore
        _mem_budget: "int | None"
        _mem_deny_logged: bool

    def _workspace_break_active(self, workspace_dir: str) -> bool:
        """True iff dispatch to ``workspace_dir`` is currently held by the
        circuit-breaker. Auto-clears an expired break so the meta table doesn't
        grow with dead keys — same lazy-clear the global pause uses."""
        until, _ = self._store.get_workspace_break(workspace_dir)
        if until == 0:
            return False
        if _now_ms() < until:
            return True
        self._store.clear_workspace_break(workspace_dir)
        return False

    def _check_and_trip_breaker(self, workspace_dir: str, task_id: str) -> None:
        """Called after a task failure. If the workspace has now crossed the
        threshold within the sliding window AND no break is already active, trip
        one and emit a breaker event. One-shot per hold — subsequent failures
        during an active break don't re-fire (avoids notify spam)."""
        if WORKSPACE_BREAK_THRESHOLD <= 0:
            return
        if self._workspace_break_active(workspace_dir):
            return  # already tripped — the hold is running
        since_ms = _now_ms() - int(WORKSPACE_BREAK_WINDOW_S * 1000)
        count = self._store.count_recent_task_failures(workspace_dir, since_ms)
        if count < WORKSPACE_BREAK_THRESHOLD:
            return
        until_ms = _now_ms() + int(WORKSPACE_BREAK_HOLD_S * 1000)
        reason = (
            f"circuit-breaker: {count} task failures in "
            f"{WORKSPACE_BREAK_WINDOW_S:.0f}s on {workspace_dir}"
        )
        self._store.set_workspace_break(workspace_dir, until_ms, reason)
        self._store.append_event(
            task_id=task_id,
            program_id=None,
            type="workspace_break_tripped",
            source="devclaw",
            payload_json=json.dumps({
                "workspace_dir": workspace_dir,
                "count": count,
                "window_s": WORKSPACE_BREAK_WINDOW_S,
                "hold_s": WORKSPACE_BREAK_HOLD_S,
                "until_ms": until_ms,
                "reason": reason,
            }),
        )
        sys.stderr.write(
            f"task-queue: workspace break tripped for {workspace_dir} "
            f"({count} failures in {WORKSPACE_BREAK_WINDOW_S:.0f}s) — "
            f"holding dispatch {WORKSPACE_BREAK_HOLD_S:.0f}s\n"
        )

    def _mem_can_launch(self) -> bool:
        """True if there's host RAM for one more sandbox — or if memory can't be
        measured (fail OPEN: an unmeasurable host must never wedge the queue). On
        denial, log ONCE per pump (loud, not a silent throttle), mirroring the
        operator-hold logging pattern so a memory hold is visible in the logs."""
        budget = self._mem_budget
        if budget is None or budget >= MEM_LAUNCH_FLOOR_BYTES:
            return True
        if not getattr(self, "_mem_deny_logged", False):
            sys.stderr.write(
                f"task-queue: dispatch held — host MemAvailable {budget >> 20}MB "
                f"< floor {MEM_LAUNCH_FLOOR_BYTES >> 20}MB (sandbox "
                f"{SANDBOX_MEMORY_BYTES >> 20}MB + reserve "
                f"{COGNITION_MEM_RESERVE_BYTES >> 20}MB); deferring launches so the "
                f"host claude --print isn't OOM-killed\n"
            )
            self._mem_deny_logged = True
        return False

    def _mem_commit_launch(self) -> None:
        """Optimistically debit one sandbox's ceiling from this pump's budget so N
        pending tasks can't all launch into RAM that only fits one (a fresh
        container's RSS lags its start). The budget is re-measured from /proc each
        pump, so a finished container's memory returns on the next tick."""
        if self._mem_budget is not None:
            self._mem_budget -= SANDBOX_MEMORY_BYTES
