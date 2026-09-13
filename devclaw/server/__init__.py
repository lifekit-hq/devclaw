"""DevClaw MCP server.

- ``_state``    — FastMCP instance + long-lived services (store, queue, goals, registry).
- ``tools``     — every ``@mcp.tool``.
- ``http``      — every ``@mcp.custom_route`` (console + JSON feeds).
- ``lifecycle`` — ``main()``, the stdio/http serve loops, the auth middleware.
"""

from ._state import goals, mcp, queue, registry, store  # noqa: F401
from . import tools  # noqa: F401  — registers @mcp.tool decorators
from . import http  # noqa: F401  — registers @mcp.custom_route handlers
from .lifecycle import main  # noqa: F401
