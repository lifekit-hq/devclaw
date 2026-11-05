"""The no-mistakes state lives on the durable volume and a redeploy cannot wedge it.

The pipeline's home (gates, run history, config, daemon files) is ``NM_HOME``.
Left at the default ``~/.no-mistakes`` it sits in the container's writable
layer: every redeploy empties it and a session finds no gate to ship through
(2026-11-05). So compose names ``NM_HOME`` under the
``devclaw-state`` mount, and the entrypoint — which runs on every container
start — clears the previous container's stale daemon files (no-mistakes trusts a
live pid, and a pid recorded by a dead container is reused at once) while
keeping everything else. No docker: the entrypoint is plain sh.
"""

import os
import subprocess
from pathlib import Path, PurePosixPath

import yaml

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
COMPOSE = yaml.safe_load((DEPLOY / "docker-compose.devclaw.yml").read_text(encoding="utf-8"))
ENTRYPOINT = DEPLOY / "entrypoint.sh"


def test_compose_puts_nm_home_on_the_state_volume_and_keeps_the_entrypoint():
    service = COMPOSE["services"]["devclaw-mcp"]
    home = PurePosixPath(service["environment"]["NM_HOME"])
    targets = [PurePosixPath(v.split(":")[1]) for v in service["volumes"]
               if isinstance(v, str) and v.startswith("devclaw-state:")]
    assert targets, "compose no longer mounts the devclaw-state volume"
    assert any(t in home.parents for t in targets)
    assert "entrypoint" not in service
    assert os.access(ENTRYPOINT, os.X_OK)


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
