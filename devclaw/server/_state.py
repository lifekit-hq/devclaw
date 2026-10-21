"""Module-level state for the devclaw MCP server: the FastMCP instance and
the long-lived services (state store, task queue, goal service, registry).
Imported by ``tools``, ``http`` and ``lifecycle`` — they attach decorators or
call methods; they never create state."""

from __future__ import annotations

import sys
import urllib.parse

from .._env_loader import load_dotenv as _load_dotenv

_load_dotenv()

from fastmcp import FastMCP  # noqa: E402

from .. import __version__  # noqa: E402
from .. import config as _config  # noqa: E402
from ..goal.service import GoalService  # noqa: E402
from ..project_registry import ProjectRegistry  # noqa: E402
from ..state_store import StateStore  # noqa: E402
from ..task_queue import TaskQueue  # noqa: E402

SERVER_NAME = "devclaw"
DB_PATH = _config.db_path()
HTTP_PORT = _config.HTTP_PORT
HTTP_HOST = _config.HTTP_HOST
AUTH_TOKEN = _config.AUTH_TOKEN
TOKEN_QS = f"?token={urllib.parse.quote(AUTH_TOKEN)}" if AUTH_TOKEN else ""

store = StateStore(DB_PATH)

_engine = _config.ENGINE
if _engine == "stub":
    from ..engine.stub import stub_engine

    sys.stderr.write("⚠ DEVCLAW_ENGINE=stub — deterministic stub engine (NO claude).\n")
    queue = TaskQueue(store, runner=stub_engine)
else:
    queue = TaskQueue(store)

registry = ProjectRegistry(DB_PATH)
goals = GoalService(queue, store, project_registry=registry)

mcp: FastMCP = FastMCP(SERVER_NAME, version=__version__)
