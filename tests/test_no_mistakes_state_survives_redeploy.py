"""The no-mistakes state lives on the durable volume and a redeploy cannot wedge it.

The pipeline's home (gates, run history, config, daemon files) is ``NM_HOME``.
Left at the default ``~/.no-mistakes`` it sits in the container's writable
layer: every redeploy empties it and a session finds no gate to ship through
(2026-11-05). So image and compose name the SAME ``NM_HOME`` under the
``devclaw-state`` mount, and the entrypoint — which runs on every container
start — clears the previous container's stale daemon files (no-mistakes trusts a
live pid, and a pid recorded by a dead container is reused at once) while
keeping everything else. No docker: the entrypoint is plain sh.
"""

import os
import re
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (DEPLOY / "docker-compose.devclaw.yml").read_text(encoding="utf-8")
ENTRYPOINT = DEPLOY / "entrypoint.sh"


def _nm_home(text: str) -> str:
    m = re.search(r"^\s*(?:ENV\s+|\s)?NM_HOME[=:]\s*(\S+?)\s*\\?$", text, re.MULTILINE)
    assert m, "NM_HOME is not set"
    return m.group(1)


def test_image_and_compose_name_one_nm_home_on_the_state_volume():
    home = _nm_home(DOCKERFILE)
    assert home == _nm_home(COMPOSE)
    mount = re.search(r"^\s*-\s+devclaw-state:(\S+)\s*$", COMPOSE, re.MULTILINE)
    assert mount, "compose no longer mounts the devclaw-state volume"
    assert home.startswith(mount.group(1).rstrip("/") + "/")


def test_the_entrypoint_runs_first_and_hands_over_to_the_server():
    assert os.access(ENTRYPOINT, os.X_OK)
    assert re.search(r'^ENTRYPOINT \["/app/deploy/entrypoint\.sh"\]$', DOCKERFILE, re.MULTILINE)
    assert re.search(r'^CMD \["devclaw-mcp"\]$', DOCKERFILE, re.MULTILINE)


def test_the_entrypoint_clears_only_the_dead_daemons_files(tmp_path):
    home = tmp_path / "nm"
    (home / "repos" / "abc.git").mkdir(parents=True)
    for name in ("daemon.pid", "daemon.lock", "socket", "state.sqlite", "config.yaml"):
        (home / name).write_text("x")
    done = subprocess.run([str(ENTRYPOINT), "echo", "handed-over"], capture_output=True, text=True,
                          env={**os.environ, "NM_HOME": str(home)})
    assert done.returncode == 0 and done.stdout.strip() == "handed-over"
    assert sorted(p.name for p in home.iterdir()) == ["config.yaml", "repos", "state.sqlite"]
    assert (home / "repos" / "abc.git").is_dir()
