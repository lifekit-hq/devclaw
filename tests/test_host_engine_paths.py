"""The host engine's derived paths must resolve against the REPOSITORY ROOT.

``runner/`` and its skill bundle are siblings of the ``devclaw/`` package, not
children of it. ``host.py`` resolved its root to ``parents[1]`` — the package
dir — so every derived default pointed one level short:

    RUNNER_PY -> devclaw/runner/runner.py     (has never existed)

``DEVCLAW_ENGINE=host`` therefore only ever ran with an explicit
``DEVCLAW_RUNNER_PY``, while ``docs/reference/env-vars.md`` advertised the
default as "``runner/runner.py`` (resolved against repo)". Nothing caught it
because nothing asserted the defaults resolve. These do.
"""
from pathlib import Path

from devclaw.engine import host

_ROOT = Path(__file__).resolve().parents[1]


def test_host_runner_py_default_resolves_to_a_real_file():
    """Named regression: the host engine's runner path exists on disk.

    A non-existent default is not a latent typo — it is host mode being broken
    for anyone who does not know to set DEVCLAW_RUNNER_PY.
    """
    assert Path(host.RUNNER_PY) == _ROOT / "runner" / "runner.py"
    assert Path(host.RUNNER_PY).is_file(), (
        f"host engine would spawn a runner that does not exist: {host.RUNNER_PY}"
    )


def test_host_skill_bundle_default_resolves_to_the_canonical_source():
    """Host mode reads the SAME skills the sandbox image bakes (#613).

    Nothing mounts /opt/devclaw/skills on the host, and since the embedded
    fallback was deleted a runner with no bundle refuses to brief the worker at
    all — so this default is what makes host mode work, not a convenience.
    """
    assert Path(host.SKILLS_DIR) == _ROOT / "runner" / "skills"
    assert Path(host.SKILLS_DIR).is_dir()
    # the bundle the image bakes from — not an empty dir that would still refuse
    assert (Path(host.SKILLS_DIR) / "_common.md").is_file()


def test_host_hooks_default_resolves_to_the_in_repo_bundle():
    assert Path(host.HOOKS_DIR) == _ROOT / "runner" / "hooks"
    assert Path(host.HOOKS_DIR).is_dir()


def test_runner_env_carries_the_skill_and_hook_dirs_to_the_subprocess():
    """The runner is spawned as a subprocess, so the dirs must travel in its env.

    Setting them on the module is not enough — nothing else exports them, and a
    subprocess inheriting the host's bare environment would find no bundle and
    refuse.
    """
    env = host._runner_env()
    assert env["DEVCLAW_SKILLS_DIR"] == host.SKILLS_DIR
    assert env["DEVCLAW_HOOKS_DIR"] == host.HOOKS_DIR


def test_runner_env_still_strips_api_keys(monkeypatch):
    """The OAuth-only invariant is not weakened by the added env entries."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-survive")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-not-survive")
    env = host._runner_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
