"""The instance's credentials are wired end to end — and stay wired.

`claude setup-token` mints the credential that keeps this box off the
revocable interactive login (#644); the `read:packages` token is what lets a
sandbox `npm ci` resolve GitHub Packages. They only help if they actually reach
the containers, and that path crosses files nobody runs locally: the repo
Actions secrets → the deploy workflow's step env → `deploy-devclaw.sh` → the
on-box secrets file → the compose `env_file`. Since tinyspec
durable-container-secrets (2026-09-04) the contract is ONE durable home and
LOUD absence: on 2026-09-03 a hand recreate resolved both `${VAR:-}` to blank,
the instance reported healthy for ~20h and a worker burned a session on an
`npm ci` 401. These pin the wiring so a rot in one file can't disarm it
unnoticed — in either direction (a credential quietly re-added to the
`environment:` block would override the file with blank again).
"""

from pathlib import Path

import pytest
import yaml

from devclaw.boot_guard import REQUIRED_PRODUCTION_ENV, SECRETS_FILE_DEFAULT

_REPO = Path(__file__).resolve().parents[1]


def _compose_service():
    compose = yaml.safe_load((_REPO / "deploy/docker-compose.devclaw.yml").read_text())
    return compose["services"]["devclaw-mcp"]


def test_compose_declares_the_secrets_file_as_the_one_home():
    svc = _compose_service()
    env_files = svc.get("env_file") or []
    # Declared in the compose FILE (not a --env-file flag a hand run can
    # forget) with the default path baked in, so a bare `docker compose up`
    # from the box reads the same file the deploy wrote.
    assert any(SECRETS_FILE_DEFAULT in str(e) for e in env_files), env_files


@pytest.mark.parametrize("var", REQUIRED_PRODUCTION_ENV)
def test_compose_never_interpolates_a_credential_in_the_environment_block(var):
    # An `environment:` entry overrides `env_file`, and `${VAR:-}` resolves to
    # blank without a shell env — the exact 2026-09-03 wipe.
    assert var not in _compose_service()["environment"]


@pytest.mark.parametrize("var", REQUIRED_PRODUCTION_ENV)
def test_deploy_workflow_hands_each_secret_to_the_deploy_step(var):
    wf = yaml.safe_load((_REPO / ".github/workflows/deploy.yml").read_text())
    steps = wf["jobs"]["deploy"]["steps"]
    deploy_steps = [s for s in steps if "deploy-devclaw.sh" in str(s.get("run", ""))]
    assert deploy_steps, "no step invokes deploy-devclaw.sh"
    for step in deploy_steps:
        assert step.get("env", {}).get(var) == "${{ secrets." + var + " }}", (
            "the deploy step must receive the credential from the repo Actions "
            "secret — the deploy script writes the on-box home from it"
        )


def test_deploy_script_writes_the_home_dies_on_absence_and_never_echoes_a_value():
    src = (_REPO / "deploy/deploy-devclaw.sh").read_text()
    # both credentials are resolved and written, by name
    for var in REQUIRED_PRODUCTION_ENV:
        assert f"_resolve_secret {var}" in src
        assert f"{var}=%s" in src  # printf'd into the home
    assert SECRETS_FILE_DEFAULT in src
    # absence is a `die`, never a warning-and-continue; the mounted-login
    # fallback is retired
    assert "is not supplied by the workflow" in src
    assert "is not set (neither in the environment nor in" in src
    assert "mounted ~/.claude login" not in src
    # …and the value is never printed: no echo/say/printf of a credential
    # expansion outside the single write into the home
    for var in REQUIRED_PRODUCTION_ENV:
        assert f'echo "${{{var}}}"' not in src
        assert f'say "${{{var}}}"' not in src
    assert src.count('"$_oauth" "$_reg" > "$SECRETS_FILE"') == 1
