"""Shared read-only context handed to every doctor check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..goal.models import Goal
    from ..goal.store import GoalStore
    from ..project_registry import ProjectRegistry
    from ..state_store import StateStore


@dataclass
class InstanceContext:
    store: "StateStore"
    goal_store: "GoalStore"
    registry: "ProjectRegistry"
    #: every goal's facts, preloaded once by the facade (best-effort — a goal
    #: whose yaml fails to load is surfaced as its own unknown finding).
    goals: "list[Goal]" = field(default_factory=list)
