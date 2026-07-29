"""Project dev-container composition (P1 of devclaw-environment-as-devcontainer).

The agent sandbox for a project that owns a ``.devcontainer/Dockerfile`` is
built as ``FROM <project-dev-image>`` + devclaw's harness layer, so the agent
runs in the same environment a human dev would. These tests pin the PURE
contract (detection, deterministic tags, build argv) and the orchestrator's
fail-CLOSED posture — the actual ``docker build`` is a live-shakedown concern
(the stub suite has no docker), exactly like sandcastle's docker-run argv is
unit-tested pure while the real run is not.
"""

from __future__ import annotations

import os

import pytest

from devclaw.engine import project_image as pi


# ---- detection ----


def test_no_devcontainer_detects_nothing(tmp_path):
    assert pi.find_dev_dockerfile(str(tmp_path)) is None


def test_devcontainer_dockerfile_is_found(tmp_path):
    dc = tmp_path / ".devcontainer"
    dc.mkdir()
    (dc / "Dockerfile").write_text("FROM mcr.microsoft.com/dotnet/sdk:9.0\n")
    found = pi.find_dev_dockerfile(str(tmp_path))
    assert found == str(dc / "Dockerfile")


def test_devcontainer_dir_without_dockerfile_detects_nothing(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    assert pi.find_dev_dockerfile(str(tmp_path)) is None


# ---- deterministic tags ----


def test_image_tags_are_deterministic_and_project_scoped(tmp_path):
    ws = tmp_path / "lifekit-dashboard"
    ws.mkdir()
    base, agent = pi.project_image_tags(str(ws))
    assert base == "devclaw-projbase-lifekit-dashboard:latest"
    assert agent == "devclaw-agent-lifekit-dashboard:latest"
    # stable across calls / trailing slash
    assert pi.project_image_tags(str(ws) + "/") == (base, agent)


def test_image_tags_sanitize_hostile_basenames():
    # basename with spaces + punctuation → docker-tag-safe slug
    base, agent = pi.project_image_tags("/x/Weird Name!!")
    assert base == "devclaw-projbase-weird-name:latest"
    assert agent == "devclaw-agent-weird-name:latest"


def test_image_tags_degenerate_basename_falls_back(tmp_path):
    base, agent = pi.project_image_tags("/")
    assert base == "devclaw-projbase-project:latest"
    assert agent == "devclaw-agent-project:latest"


# ---- pure build argv ----


def test_base_build_argv_uses_devcontainer_folder_as_context():
    argv = pi.base_build_argv("/w/.devcontainer", "/w/.devcontainer/Dockerfile", "t:latest")
    assert argv == ["build", "-t", "t:latest", "-f", "/w/.devcontainer/Dockerfile", "/w/.devcontainer"]


def test_agent_build_argv_passes_base_image_build_arg():
    argv = pi.agent_build_argv("/repo", "/repo/.sandcastle/Dockerfile", "agent:latest", "base:latest")
    assert argv == [
        "build", "-t", "agent:latest", "-f", "/repo/.sandcastle/Dockerfile",
        "--build-arg", "BASE_IMAGE=base:latest", "/repo",
    ]


def test_harness_paths_point_at_this_checkout():
    dockerfile, context = pi.harness_paths()
    assert dockerfile.endswith(os.path.join(".sandcastle", "Dockerfile"))
    assert os.path.isfile(dockerfile), "harness Dockerfile must exist in the checkout"
    assert os.path.isdir(os.path.join(context, "openhands-runner")), (
        "harness build context must be the repo root (it COPYs openhands-runner/)"
    )


# ---- orchestrator (injected runner — no docker) ----


class _Runner:
    """Records the docker argvs it's asked to run; returns a scripted rc/tail."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    async def __call__(self, argv):
        self.calls.append(argv)
        return self.results.pop(0)


async def test_ensure_agent_image_returns_none_without_devcontainer(tmp_path):
    runner = _Runner([])
    got = await pi.ensure_agent_image(str(tmp_path), run=runner)
    assert got is None
    assert runner.calls == []  # no build attempted → zero-cost no-op


async def test_ensure_agent_image_builds_base_then_harness_and_returns_agent_tag(tmp_path):
    ws = tmp_path / "dash"
    (ws / ".devcontainer").mkdir(parents=True)
    (ws / ".devcontainer" / "Dockerfile").write_text("FROM mcr.microsoft.com/dotnet/sdk:9.0\n")
    runner = _Runner([(0, "built base"), (0, "built agent")])

    got = await pi.ensure_agent_image(str(ws), run=runner)

    assert got == "devclaw-agent-dash:latest"
    assert len(runner.calls) == 2
    # 1) project dev image built from its own Dockerfile
    assert runner.calls[0][:3] == ["build", "-t", "devclaw-projbase-dash:latest"]
    # 2) harness composed FROM it via the BASE_IMAGE build-arg
    assert "--build-arg" in runner.calls[1]
    assert "BASE_IMAGE=devclaw-projbase-dash:latest" in runner.calls[1]
    assert runner.calls[1][:3] == ["build", "-t", "devclaw-agent-dash:latest"]


async def test_ensure_agent_image_fails_closed_when_base_build_fails(tmp_path):
    ws = tmp_path / "dash"
    (ws / ".devcontainer").mkdir(parents=True)
    (ws / ".devcontainer" / "Dockerfile").write_text("FROM nope\n")
    runner = _Runner([(1, "no such image: nope")])

    with pytest.raises(pi.ProjectImageBuildError) as exc:
        await pi.ensure_agent_image(str(ws), run=runner)
    assert "project dev image" in str(exc.value)
    assert "no such image: nope" in str(exc.value)
    assert len(runner.calls) == 1  # harness build never attempted after base fails


async def test_ensure_agent_image_fails_closed_when_harness_build_fails(tmp_path):
    ws = tmp_path / "dash"
    (ws / ".devcontainer").mkdir(parents=True)
    (ws / ".devcontainer" / "Dockerfile").write_text("FROM mcr.microsoft.com/dotnet/sdk:9.0\n")
    runner = _Runner([(0, "built base"), (137, "OOMKilled")])

    with pytest.raises(pi.ProjectImageBuildError) as exc:
        await pi.ensure_agent_image(str(ws), run=runner)
    assert "harness" in str(exc.value)
    assert "OOMKilled" in str(exc.value)
