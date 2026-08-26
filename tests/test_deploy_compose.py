"""Deploy-fragment guards.

The stubbed suite cannot see a deployed box, so drift between the documented
operator knobs and what the compose fragment actually passes into the
devclaw-mcp container is invisible to every behavioral test (#641's class).
These tests pin the fragment structurally.

2026-08-26 incident: DEVCLAW_SANDBOX_MEMORY was documented in
docs/reference/env-vars.md and read by devclaw/config.py, but the deploy
fragment never substituted it into the container environment — so the
operator remedy for a sandbox OOM (raise the cap in /srv/devclaw/.env) was a
silent no-op.
"""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "docker-compose.devclaw.yml"

# knob -> the default devclaw/config.py applies when the env var is unset.
# The compose substitution default must match, so an unset knob is a no-op.
SIZING_KNOBS = {
    "DEVCLAW_SANDBOX_MEMORY": "2g",
    "DEVCLAW_SANDBOX_CPUS": "2.0",
    "DEVCLAW_COGNITION_MEM_RESERVE": "1536m",
}


def test_sandbox_sizing_knobs_are_passed_through_to_devclaw_mcp():
    text = COMPOSE.read_text()
    for var, default in SIZING_KNOBS.items():
        line = f"{var}: ${{{var}:-{default}}}"
        assert line in text, (
            f"deploy/docker-compose.devclaw.yml must substitute {line!r} into "
            "the devclaw-mcp environment block — without it the operator knob "
            "in /srv/devclaw/.env never reaches the container"
        )


def test_sizing_knob_compose_defaults_match_config_py():
    config = (COMPOSE.parents[1] / "devclaw" / "config.py").read_text()
    for var, default in SIZING_KNOBS.items():
        m = re.search(rf'environ\.get\("{var}", "([^"]+)"\)', config)
        assert m, f"{var} not read via os.environ.get in devclaw/config.py"
        assert m.group(1) == default, (
            f"{var}: compose fragment default {default!r} drifted from "
            f"devclaw/config.py default {m.group(1)!r}"
        )
