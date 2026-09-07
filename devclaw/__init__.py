"""DevClaw — a thin orchestration layer in front of a coding agent.

This is the all-Python host runtime. The MCP server, SQLite state store, task
queue, and docker-sandbox launcher all live here. The only other code is
``runner/runner.py``, the worker harness that runs *inside* the per-task
sandbox container and drives the agent over ACP (spec 011).
"""

__all__ = ["__version__"]

try:  # populated from package metadata once installed
    from importlib.metadata import version as _version

    __version__ = _version("devclaw")
except Exception:  # running from a source tree that isn't installed
    __version__ = "1.1.0"  # x-release-please-version
