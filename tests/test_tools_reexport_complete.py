"""Every registered MCP tool resolves via ``getattr(tools, name)``.

The ``server/tools`` package promises: "Every tool callable is re-exported
below so ``getattr(tools, name)`` keeps resolving the full menu (the eval
harness picks tools by name)." That promise had no structural guard, so
``set_goal_verify_cmd`` was registered but silently missing from the
re-export list — live over MCP, invisible to the harness. This pins the
class: a tool added to any submodule without its re-export fails here.
"""

from __future__ import annotations

import asyncio


def test_every_registered_tool_is_reexported_from_tools_package():
    from devclaw.server import tools
    from devclaw.server._state import mcp

    registered = [t.name for t in asyncio.run(mcp.list_tools())]
    assert registered, "no tools registered — the import side effect broke"
    missing = [name for name in registered if not hasattr(tools, name)]
    assert not missing, (
        f"registered tools missing from the server/tools re-export list: {missing}"
    )
