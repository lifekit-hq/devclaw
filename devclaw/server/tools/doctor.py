"""Doctor — read-only instance diagnostics; zero writes, zero cognition."""

from __future__ import annotations

import json

from ...doctor import run_doctor
from .._state import mcp, registry, store


@mcp.tool
async def doctor() -> str:
    """Mechanical, read-only checks over the running instance: required
    credentials present and live, the database reachable, the quota pause,
    every open goal's checkout, every project's workspace. Each finding
    names its remedy; doctor executes none."""
    return json.dumps(run_doctor(store, registry).to_dict(), indent=2)
