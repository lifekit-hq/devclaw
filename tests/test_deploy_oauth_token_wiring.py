"""The instance's OAuth credential is wired end to end — and stays wired.

`claude setup-token` mints the credential that keeps this box off the revocable
interactive login (#644). It only helps if it actually reaches the containers,
and that path crosses three files nobody runs locally: the repo Actions secret →
the deploy workflow's step env → compose interpolation. Each hop is a silent
no-op when it breaks: the deploy still succeeds and the instance quietly falls
back to the mounted ~/.claude login, which is exactly the 2026-08-22 outage.
These pin the wiring so a rot in one file can't disarm it unnoticed.
"""

from pathlib import Path

import yaml


_REPO = Path(__file__).resolve().parents[1]
_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def test_compose_interpolates_the_token_with_a_blank_default():
    compose = yaml.safe_load((_REPO / "deploy/docker-compose.devclaw.yml").read_text())
    env = compose["services"]["devclaw-mcp"]["environment"]
    # Blank default, not a required var: an instance with no token deploys and
    # runs on the mounted credential rather than failing to start.
    assert env[_VAR] == "${" + _VAR + ":-}"


def test_deploy_workflow_hands_the_secret_to_the_deploy_step():
    wf = yaml.safe_load((_REPO / ".github/workflows/deploy.yml").read_text())
    steps = wf["jobs"]["deploy"]["steps"]
    deploy_steps = [s for s in steps if "deploy-devclaw.sh" in str(s.get("run", ""))]
    assert deploy_steps, "no step invokes deploy-devclaw.sh"
    for step in deploy_steps:
        assert step.get("env", {}).get(_VAR) == "${{ secrets." + _VAR + " }}", (
            "the deploy step must receive the token from the repo Actions "
            "secret — compose reads it from the shell env"
        )


def test_deploy_script_warns_when_no_credential_is_supplied():
    # The fallback must be LOUD. Absent from both the env and the env file, the
    # script still deploys but says so — a silent fallback is how a box ends up
    # on a credential a PC login can revoke without anyone noticing.
    src = (_REPO / "deploy/deploy-devclaw.sh").read_text()
    assert _VAR in src
    assert "mounted ~/.claude login" in src
    # …and never prints the value itself.
    assert f'echo "${{{_VAR}}}"' not in src
    assert f"${_VAR}\"" not in src.replace(f'"${{{_VAR}:-}}"', "")
