"""Compose a project's dev container with devclaw's harness — P1 of the
"environment is one repo-owned dev-container, shared by human and agent"
direction (vault: devclaw-environment-as-devcontainer).

When a project owns a ``.devcontainer/Dockerfile`` (a DEV environment — the
SDK/tools a human developing the project would use, NOT its slim prod image),
the agent sandbox is built as ``FROM <project-dev-image>`` + devclaw's harness
layer (``.sandcastle/Dockerfile`` with ``BASE_IMAGE`` set). The agent then runs
inside the SAME environment a human dev would — no more re-deriving the
toolchain at runtime (mise detect/install/PATH/resync, issues #421/#423/#424),
and no human↔agent environment drift by construction.

Composition, not coupling: the project's Dockerfile declares only its toolchain
(claude-free, human-usable); this layer adds devclaw's harness on top, so the
project image stays model-agnostic.

Split by testability, mirroring ``sandcastle.py``: the tag / argv / detection
helpers are PURE (no docker, no I/O beyond a stat) and unit-tested; the async
``ensure_agent_image`` orchestrator shells out to docker and fails CLOSED — a
build that cannot produce the image raises, and the caller errors the task. A
build failure is never a silent fallback to the bare default image (the
hardening philosophy: loud, not silent).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

#: A project declares its dev environment here — the devcontainer convention.
#: (Only the Dockerfile is read in P1; devcontainer.json build-options parsing
#: is a later slice.)
DEVCONTAINER_DOCKERFILE = os.path.join(".devcontainer", "Dockerfile")

#: docker tag component grammar — lowercase alnum + a small punct set, exactly
#: the sanitizer shape ``_toolchain_volume_name`` already uses so a project maps
#: to one deterministic image across tasks.
_TAG_SAFE = re.compile(r"[^a-z0-9_.-]+")


def find_dev_dockerfile(workspace_dir: str) -> Optional[str]:
    """Absolute path to the project's ``.devcontainer/Dockerfile`` if it exists,
    else ``None`` (→ the caller falls back to the default sandbox image). A pure
    stat, no subprocess — so an undeclared project stays a zero-cost no-op, the
    same posture as ``_detect_toolchain`` returning ``(False, {})``."""
    path = os.path.join(workspace_dir, DEVCONTAINER_DOCKERFILE)
    return path if os.path.isfile(path) else None


def _sanitize(basename: str) -> str:
    """A docker-tag-safe slug of the workspace basename. Lowercased, unsafe runs
    collapsed to ``-``, trimmed; empty/degenerate → ``project`` so a hostile or
    blank basename can never emit an invalid ``-t`` argument."""
    slug = _TAG_SAFE.sub("-", basename.lower()).strip("-._")
    return slug or "project"


def project_image_tags(workspace_dir: str) -> tuple[str, str]:
    """``(base_tag, agent_tag)`` — the project's dev image and the harness-
    composed agent image, deterministic per project so ``docker build`` reuses
    the layer cache across that project's tasks (a Dockerfile change rebuilds
    only the affected layers). ``:latest`` is intentional: the build is
    idempotent and always re-run before dispatch, so the tag is a stable handle,
    not a version pin."""
    slug = _sanitize(os.path.basename(os.path.normpath(workspace_dir)))
    return f"devclaw-projbase-{slug}:latest", f"devclaw-agent-{slug}:latest"


def harness_paths() -> tuple[str, str]:
    """``(harness_dockerfile, harness_context)`` — devclaw's own
    ``.sandcastle/Dockerfile`` and repo root (its context, since it COPYs
    ``openhands-runner/``), located relative to this installed package. Assumes
    devclaw runs from a source checkout (the live VPS deploy is one); a bare
    pip-install without ``.sandcastle/`` would raise at build time, loud."""
    root = Path(__file__).resolve().parents[2]
    return str(root / ".sandcastle" / "Dockerfile"), str(root)


def base_build_argv(context_dir: str, dockerfile: str, tag: str) -> list[str]:
    """``docker`` argv (sans the ``docker`` bin) to build the project's dev
    image from its own Dockerfile. Context is the Dockerfile's directory
    (``.devcontainer/``) — a dev-env Dockerfile installs tools, it does not COPY
    the app (the workspace is bind-mounted at run time), so the small
    devcontainer folder is the right, cheap context. Pure."""
    return ["build", "-t", tag, "-f", dockerfile, context_dir]


def agent_build_argv(
    harness_context: str, harness_dockerfile: str, tag: str, base_tag: str
) -> list[str]:
    """``docker`` argv to build the harness-composed agent image FROM the
    project's dev image (``--build-arg BASE_IMAGE=<base_tag>``). Pure."""
    return [
        "build",
        "-t",
        tag,
        "-f",
        harness_dockerfile,
        "--build-arg",
        f"BASE_IMAGE={base_tag}",
        harness_context,
    ]


class ProjectImageBuildError(Exception):
    """A project dev-container image that cannot be built. The message is the
    task-actionable reason (which build failed + docker's tail) — the caller
    errors the task with it, never a silent fallback."""


#: A build runner: given docker argv, resolve to ``(returncode, output_tail)``.
#: Injected so the orchestrator is unit-testable without docker.
BuildRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]


async def ensure_agent_image(
    workspace_dir: str, *, run: BuildRunner
) -> Optional[str]:
    """Build (idempotently) and return the harness-composed agent image tag for
    a project that owns a ``.devcontainer/Dockerfile``; ``None`` when it doesn't
    (→ the caller uses the default sandbox image). Two ``docker build``s — the
    project dev image, then the harness FROM it — each fail CLOSED: a non-zero
    build raises :class:`ProjectImageBuildError` with the reason, and the task
    errors rather than silently running in the wrong environment."""
    dev_dockerfile = find_dev_dockerfile(workspace_dir)
    if dev_dockerfile is None:
        return None
    base_tag, agent_tag = project_image_tags(workspace_dir)
    harness_dockerfile, harness_context = harness_paths()
    dev_context = os.path.dirname(dev_dockerfile)

    rc, tail = await run(base_build_argv(dev_context, dev_dockerfile, base_tag))
    if rc != 0:
        raise ProjectImageBuildError(
            f"building the project dev image from {DEVCONTAINER_DOCKERFILE} "
            f"failed (exit {rc}): {tail}"
        )
    rc, tail = await run(
        agent_build_argv(harness_context, harness_dockerfile, agent_tag, base_tag)
    )
    if rc != 0:
        raise ProjectImageBuildError(
            f"composing the devclaw harness onto the project dev image failed "
            f"(exit {rc}): {tail}"
        )
    return agent_tag
