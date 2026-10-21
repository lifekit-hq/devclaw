"""Admission — the dispatch-admission brake of the queue, split out as a mixin.

:class:`AdmissionMixin` carries the per-workspace failure circuit-breaker
consulted by ``_pump``. Its tuning constants (``WORKSPACE_BREAK_*``) are bound
HERE — tests that tune the breaker patch THIS namespace.

A mixin on the SAME instance — every method here runs against the
``self._store`` the base ``TaskQueue`` owns. This module must never import
``devclaw.task_queue`` at runtime (the dependency points the other way).
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from ..state_store import _now_ms

if TYPE_CHECKING:
    from ..state_store import StateStore

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
