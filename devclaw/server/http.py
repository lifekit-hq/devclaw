"""HTTP route registration.

Every console-facing route lives in :mod:`devclaw.server.routes`, split by
resource. This module exists to IMPORT those modules: ``@mcp.custom_route``
registers at import time, so a route module nothing imports serves nothing.

It used to hold all forty routes inline (~1,830 lines). The split is by
resource, not by verb — see ``routes/__init__`` for the two conventions that
make it safe (registration-is-import, and per-module state rebinding).
"""

from __future__ import annotations

# Registration side effects. NOT unused imports — see the module docstring.
# ORDER IS THE ROUTING TABLE: starlette matches in registration order, and
# console.py carries the legacy HTML→console redirects whose bare patterns
# (/goals/{goal_id}, /goals, /) would swallow every more-specific JSON route
# registered after them — /goals/{id}.json matched as goal_id="….json" and
# 302'd into the SPA (live-found 2026-08-24; the #625 split had alphabetized
# this block). console therefore imports LAST; the whole property is pinned
# structurally by tests/test_route_shadowing.py — do not re-alphabetize.
from .routes import control as _routes_control  # noqa: F401
from .routes import evals as _routes_evals  # noqa: F401
from .routes import goals as _routes_goals  # noqa: F401
from .routes import observability as _routes_observability  # noqa: F401
from .routes import projects as _routes_projects  # noqa: F401
from .routes import tasks as _routes_tasks  # noqa: F401
from .routes import console as _routes_console  # noqa: F401
