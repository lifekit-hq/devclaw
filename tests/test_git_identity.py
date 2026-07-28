"""devclaw's git author identity — every commit devclaw produces is authored
``devclaw`` (env-configurable), never the ambient identity of whatever
environment the commit happens in.

Regression for the 2026-07-28 live finding: worker commits went out under the
OWNER's personal git identity (leaked ambient config) while delivery commits
said a hardcoded ``devclaw@local`` — two identities, neither chosen. The
worker model's own ``Co-Authored-By: Claude …`` trailer is untouched by this:
devclaw is the author, the model stays visible as co-author.
"""

import subprocess

from devclaw import git_identity, task_git
from devclaw.engine import host
from devclaw.engine import sandcastle as sc


def test_git_identity_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("DEVCLAW_GIT_NAME", raising=False)
    monkeypatch.delenv("DEVCLAW_GIT_EMAIL", raising=False)
    assert git_identity.git_identity_env() == {
        "GIT_AUTHOR_NAME": "devclaw",
        "GIT_AUTHOR_EMAIL": "devclaw@local",
        "GIT_COMMITTER_NAME": "devclaw",
        "GIT_COMMITTER_EMAIL": "devclaw@local",
    }
    monkeypatch.setenv("DEVCLAW_GIT_NAME", "devclaw-bot")
    monkeypatch.setenv("DEVCLAW_GIT_EMAIL", "12345+devclaw-bot@users.noreply.github.com")
    env = git_identity.git_identity_env()
    assert env["GIT_AUTHOR_NAME"] == "devclaw-bot"
    assert env["GIT_COMMITTER_EMAIL"] == "12345+devclaw-bot@users.noreply.github.com"


def test_sandbox_commits_are_authored_devclaw(monkeypatch):
    """The sandbox argv pins all four GIT_* env vars — env beats every git
    config level, so an identity baked into the image or leaked through a
    mount can't put the owner's name on agent commits."""
    monkeypatch.delenv("DEVCLAW_GIT_NAME", raising=False)
    monkeypatch.delenv("DEVCLAW_GIT_EMAIL", raising=False)
    args = sc._build_docker_args(
        container_name="devclaw-deadbeef",
        host_bind_path="/host/ws",
        claude_dir="/home/u/.claude",
        payload='{"kind":"implement_feature"}',
    )
    for pair in (
        "GIT_AUTHOR_NAME=devclaw",
        "GIT_AUTHOR_EMAIL=devclaw@local",
        "GIT_COMMITTER_NAME=devclaw",
        "GIT_COMMITTER_EMAIL=devclaw@local",
    ):
        assert pair in args and args[args.index(pair) - 1] == "-e"
    # image + payload still land last, payload terminal
    assert args[-2] == sc.SANDBOX_IMAGE
    assert args[-1] == '{"kind":"implement_feature"}'


def test_wip_snapshot_commit_authored_devclaw_even_with_ambient_identity(
    monkeypatch, tmp_path
):
    """The usage-limit WIP snapshot commit is authored devclaw even when the
    process env carries someone else's GIT_* identity — the exact ambient-leak
    vector that put the owner's name on worker commits (live 2026-07-28)."""
    monkeypatch.delenv("DEVCLAW_GIT_NAME", raising=False)
    monkeypatch.delenv("DEVCLAW_GIT_EMAIL", raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Not Devclaw")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "owner@leak")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Not Devclaw")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "owner@leak")
    repo = tmp_path / "ws"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "wip.txt").write_text("half-done\n")
    assert task_git._wip_snapshot_sync(str(repo), "task1234abcd") == "committed"
    show = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%an <%ae>|%cn <%ce>"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert show == "devclaw <devclaw@local>|devclaw <devclaw@local>"


def test_host_runner_env_pins_identity_and_still_strips_api_keys(monkeypatch):
    """The host engine gets the same identity — and the OAuth-only invariant
    (API keys stripped) is untouched by the merge."""
    monkeypatch.delenv("DEVCLAW_GIT_NAME", raising=False)
    monkeypatch.delenv("DEVCLAW_GIT_EMAIL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-leak")
    env = host._runner_env()
    assert env["GIT_AUTHOR_NAME"] == "devclaw"
    assert env["GIT_COMMITTER_EMAIL"] == "devclaw@local"
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
