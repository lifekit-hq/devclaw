"""The worker image lets a session run a browser (#987) without a quiet widening.

Tripwire, not coverage: the compose file keeps Docker's default seccomp
profile, no-new-privileges and the dropped caps (widening them for Chromium's
sandbox is an owner call).
"""
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_compose_keeps_the_container_fence():
    svc = yaml.safe_load((DEPLOY / "docker-compose.devclaw.yml").read_text())["services"]["devclaw-mcp"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not any(o.startswith("seccomp") or "unconfined" in o for o in svc["security_opt"])
    assert {"NET_RAW", "NET_ADMIN"} <= set(svc["cap_drop"])
    assert "cap_add" not in svc and not svc.get("privileged")

