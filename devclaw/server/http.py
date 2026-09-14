"""HTTP route registration — importing a route module registers it. ORDER IS
THE ROUTING TABLE: starlette matches in registration order, and console.py's
bare ``/goals/{goal_id}`` pattern would swallow the ``.json`` routes, so it
imports LAST (pinned by tests/test_route_shadowing.py)."""

from __future__ import annotations

from .routes import control as _routes_control  # noqa: F401
from .routes import goals as _routes_goals  # noqa: F401
from .routes import metrics as _routes_metrics  # noqa: F401
from .routes import projects as _routes_projects  # noqa: F401
from .routes import tasks as _routes_tasks  # noqa: F401
from .routes import verdicts as _routes_verdicts  # noqa: F401
from .routes import webhooks as _routes_webhooks  # noqa: F401
from .routes import console as _routes_console  # noqa: F401
