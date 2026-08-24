"""SQLite state store for DevClaw tasks.

Wire shapes (``to_dict``) are camelCase to match the original TypeScript
output, so MCP consumers keep working across the rewrite.

The store was split into a package for legibility (behavior-preserving):

- :mod:`.rows` — the pure data (dataclasses, row mappers, literals, constants).
- :mod:`.control` — :class:`ControlPlaneMixin`, the thin typed ``meta`` wrappers.
- :mod:`.problems` — :class:`ProblemsMixin`, the deduplicated problems catalog.
- :mod:`.observability` — :class:`ObservabilityMixin`, the events/traces logs
  + their retention prunes.
- :mod:`.evals` — :class:`EvalOutcomesMixin`, the continuous-eval projections
  (eval_outcomes + cycle_reports, ADR 0006).
- :mod:`.core` — :class:`StateStore` itself: connection, transactions,
  task/program CRUD, scheduling/recovery, VACUUM + DB-size alarm.

Every public name the pre-split ``state_store.py`` exported is re-exported here,
so no importer changes.
"""

from __future__ import annotations

from .core import StateStore
from .rows import (
    SQLITE_BUSY_TIMEOUT_MS,
    Program,
    ProgramStatus,
    Task,
    TaskEvent,
    TaskKind,
    TaskStatus,
    _now_ms,
    derive_failure_class,
)

__all__ = [
    "StateStore",
    "derive_failure_class",
    "Task",
    "Program",
    "TaskEvent",
    "TaskStatus",
    "TaskKind",
    "ProgramStatus",
    "SQLITE_BUSY_TIMEOUT_MS",
    "_now_ms",
]
