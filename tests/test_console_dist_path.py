"""The console route serves the bundle from the exact path the wheel ships.

#625 moved the console routes into ``devclaw/server/routes/`` and left the
bundle path anchored to ``__file__``'s parent — every deployed ``/console``
503'd "bundle not built" while the bundle sat one directory up. This pins the
route's resolution to the location pyproject's wheel-artifacts glob names, so
the two cannot drift apart silently again.
"""

import re
from pathlib import Path

import devclaw.server as _server
from devclaw.server.routes import console as _console

_REPO = Path(__file__).resolve().parents[1]


def test_console_dist_resolves_to_the_server_package():
    assert _console._CONSOLE_DIST == Path(_server.__file__).resolve().parent / "console_dist"


def test_console_dist_matches_the_wheel_artifacts_glob():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'artifacts\s*=\s*\["([^"]+)/\*\*"\]', pyproject)
    assert m, "wheel artifacts glob not found in pyproject"
    shipped = (_REPO / m.group(1)).resolve()
    assert _console._CONSOLE_DIST.resolve() == shipped
