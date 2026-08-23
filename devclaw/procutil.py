"""The one host-side helper-subprocess boundary.

Seven modules used to carry byte-identical private copies of this wrapper
(their docstrings even said "mirrors ``delivery/repo.py``") — the #630 class
of smell: the same fact computed N ways drifts N ways. Modules that need a
*different* contract (``trend_signals``'s sync+timeout git reader,
``mergeability``'s ``-1``-on-spawn-failure probe, ``task_change``'s git
boundary) deliberately keep their own; this is the home for the common shape
only.
"""

from __future__ import annotations

import asyncio
import os


async def run(
    *argv: str,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run a command, return ``(exit_code, combined stdout+stderr)``. Never
    raises — a spawn failure (missing binary, bad cwd) returns
    ``(127, "<argv0> not runnable: …")``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **env_extra} if env_extra else None,
        )
    except OSError as exc:
        return 127, f"{argv[0]} not runnable: {exc}"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()
