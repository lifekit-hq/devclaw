"""The worker image lets a session run a browser (#987) without a quiet widening.

Tripwire, not coverage: the compose file keeps Docker's default seccomp
profile, no-new-privileges and the dropped caps (widening them for Chromium's
sandbox was refused), and the image's wrapper starts the named Chromium with its
own sandbox off, failing loud when no Chromium is named.
"""
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
WRAPPER = DEPLOY / "chromium-no-sandbox"


def test_compose_keeps_the_container_fence():
    svc = yaml.safe_load((DEPLOY / "docker-compose.devclaw.yml").read_text())["services"]["devclaw-mcp"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not any(o.startswith("seccomp") or "unconfined" in o for o in svc["security_opt"])
    assert {"NET_RAW", "NET_ADMIN"} <= set(svc["cap_drop"])
    assert "cap_add" not in svc and not svc.get("privileged")


def test_wrapper_starts_chromium_without_its_sandbox_and_fails_loud_unnamed(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    env = {**os.environ, "DEVCLAW_CHROMIUM": str(fake)}
    out = subprocess.run([str(WRAPPER), "--remote-debugging-pipe", "about:blank"], env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["--no-sandbox", "--remote-debugging-pipe", "about:blank"]
    env.pop("DEVCLAW_CHROMIUM")
    assert subprocess.run([str(WRAPPER)], env=env, capture_output=True).returncode != 0
