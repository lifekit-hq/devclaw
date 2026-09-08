"""The suite owns its environment — a hermeticity pin is never a default.

``tests/conftest.py`` pins ``DEVCLAW_DB`` and ``DEVCLAW_SKILLS_DIR`` before any
``devclaw`` import, because ``devclaw.server._state`` builds a real StateStore
and ProjectRegistry AT IMPORT TIME and the skills pin decides which copy of the
worker instructions the prompt tests read (#610).

Both used ``os.environ.setdefault``, which made them negotiable, and both lost:

* Under ``-n auto`` the xdist controller imports conftest first and execnet
  spawns each worker with the controller's environ, so a worker's setdefault was
  a no-op and every worker opened the CONTROLLER's database. Sixteen concurrent
  ``PRAGMA journal_mode = WAL`` on one file is a lock race the loaded VPS runner
  loses; the losing worker fails to import the module under collection and xdist
  aborts on the resulting collection mismatch. CI on main was red from
  2026-09-08 17:45 with no commit responsible — it tracked instance load.
* CI runs on the same host devclaw is deployed to, so an ambient value would
  simply be adopted.

These pin the invariant, not the incident: a pin that can be overridden by an
inheriting parent or an ambient host is not a pin.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_db_pin_is_per_process_not_inherited():
    """The whole point: a child process that inherits this one's environment
    must still get its OWN database path. This is the exact shape of an xdist
    worker — inheriting environ from the controller — so if this passes, the
    sixteen-workers-one-file race cannot recur."""
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import conftest, os; "
         "print(os.environ['DEVCLAW_DB'])" % str(REPO / "tests")],
        capture_output=True, text=True, env={**os.environ}, cwd=str(REPO),
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() != os.environ["DEVCLAW_DB"], (
        "a child that inherited DEVCLAW_DB reused it — the pin is a default "
        "again, and every xdist worker shares one database"
    )


def test_pins_survive_a_hostile_ambient_environment():
    """CI runs on the deploy host. An ambient DEVCLAW_DB must never reach the
    suite, and DEVCLAW_SKILLS_DIR must always resolve to the IN-REPO bundle —
    never the baked /opt/devclaw/skills/ copy production reads (#610)."""
    hostile = {**os.environ,
               "DEVCLAW_DB": "/tmp/hostile-devclaw.db",
               "DEVCLAW_SKILLS_DIR": "/opt/devclaw/skills"}
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import conftest, os; "
         "print(os.environ['DEVCLAW_DB']); print(os.environ['DEVCLAW_SKILLS_DIR'])"
         % str(REPO / "tests")],
        capture_output=True, text=True, env=hostile, cwd=str(REPO),
    )
    assert child.returncode == 0, child.stderr
    db, skills = child.stdout.strip().splitlines()
    assert db != "/tmp/hostile-devclaw.db", "an ambient DEVCLAW_DB reached the suite"
    assert skills == str(REPO / "runner" / "skills"), (
        f"DEVCLAW_SKILLS_DIR resolved to {skills!r}, not the in-repo bundle"
    )


def test_no_hermeticity_pin_uses_setdefault():
    """Structural guard: the two env pins above conftest's first devclaw import
    must be assignments. `setdefault` reads as equivalent and is not — it hands
    the decision to whoever set the variable first."""
    src = (REPO / "tests" / "conftest.py").read_text()
    head = src.split("from devclaw", 1)[0]
    # Comments explain WHY these are assignments and name the wrong verb; judge
    # the code only.
    code = "\n".join(
        ln for ln in head.splitlines() if not ln.lstrip().startswith("#")
    )
    for var in ("DEVCLAW_DB", "DEVCLAW_SKILLS_DIR"):
        assert f'os.environ["{var}"]' in code, f"{var} is not pinned by assignment"
    assert "setdefault" not in code, (
        "a hermeticity pin was made negotiable again with setdefault"
    )
