"""The durable goal layer (spec 046): one tick rule, one session prompt, one
done-gate, GitHub as the state. ~1,500 lines is the ceiling — a size guard
holds it (tests/test_goal_layer_stays_readable.py)."""

from .service import GoalService

__all__ = ["GoalService"]
