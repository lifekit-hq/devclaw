"""The worker image lets a session run a browser (#987) without a quiet widening.

Tripwires, not coverage:
- the compose file keeps Docker's default seccomp profile, no-new-privileges
  and the dropped caps (widening them for Chromium's sandbox is an owner call);
- the Chromium system libraries are installed as root, before the image drops
  to the runtime user (a session cannot apt-install them).
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


def test_chromium_libs_are_installed_as_root_before_the_runtime_user():
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    final = dockerfile[dockerfile.rindex("\nFROM "):]
    libs = final.index("playwright@latest install-deps chromium")
    first_user = final.index("\nUSER ")
    assert libs < first_user
    assert "dotnet-sdk-" in final  # finance-sentry's verify runs `dotnet`
