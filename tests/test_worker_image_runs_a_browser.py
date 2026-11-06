"""The worker image lets a session run a browser (#987) without a quiet widening.

Tripwire, not coverage: the compose file keeps Docker's default seccomp
profile, no-new-privileges and the dropped caps (widening them for Chromium's
sandbox was refused), and Chromium's own sandbox is turned off ONLY through the
image's wrapper -- the flag appears nowhere CI, local dev or a project's
committed Playwright config can see it.
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


def test_chromium_sandbox_is_off_only_in_the_container_wrapper(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    env = {**os.environ, "DEVCLAW_CHROMIUM": str(fake)}
    out = subprocess.run([str(WRAPPER), "--remote-debugging-pipe", "about:blank"], env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["--no-sandbox", "--remote-debugging-pipe", "about:blank"]
    env.pop("DEVCLAW_CHROMIUM")
    assert subprocess.run([str(WRAPPER)], env=env, capture_output=True).returncode != 0

    # The scan reaches the wrapper only through the image's env var.
    spec = (ROOT / "frontend/e2e/quality.spec.ts").read_text()
    assert "process.env['DEVCLAW_CHROMIUM_NO_SANDBOX']" in spec and "--no-sandbox" not in spec
    # No committed Playwright config or CI workflow carries the flag.
    for path in [*(ROOT / "frontend").glob("playwright.config.*"), *(ROOT / ".github").rglob("*.yml")]:
        assert "no-sandbox" not in path.read_text(), path
