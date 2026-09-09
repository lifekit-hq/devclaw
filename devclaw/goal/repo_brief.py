"""Repo-scoped pointers for the dispatch brief (spec 029 + spec 034).

The worker PULLS its repo context from the checkout; the host never pushes
it. This module holds the pointers the dispatch path prepends to the brief so the
worker knows what to read first, and the one DECLARATION it renders outright:

- :func:`architecture_map_pointer` — the repo's ``ARCHITECTURE.md`` (spec 029);
- :func:`worker_memory_pointer` — the repo's committed ``.devclaw/MEMORY.md``
  memory index (spec 034): one fact per file under ``.devclaw/memory/``,
  maintained by the worker inside its increments and reviewed like code.
- :func:`verification_environment` — the repo's declared verification
  environment and what its verify command does not cover (tinyspec
  ``verify-gate-is-never-narrower-than-ci``). This one renders content rather
  than a pointer, because it is not IN the checkout to be read: it is a
  bounded declaration from ``devclaw.json`` at the default-branch tip.

All are best-effort, never-raise, zero-LLM, and bounded — independent of how much the
pointed-at file holds — the brief's size does not grow with the repo's
memory (spec 034 FR-002). The former host-side accumulated ``REPO NOTES``
blob (``project_docs.repo_brief``, injected in full every dispatch) was
retired by spec 034: an append-only prose store nobody edited rotted into
the same fact restated fifteen times.
"""

from __future__ import annotations

import os

#: the worker memory index, relative to the checkout root (contracts/memory-layout.md).
WORKER_MEMORY_INDEX = os.path.join(".devclaw", "MEMORY.md")


def architecture_map_pointer(workspace_dir: "str | None") -> str:
    """Return a dispatch-brief section pointing the worker at ARCHITECTURE.md.

    Best-effort and never-raises: an absent file or any OS error returns "".
    The pointer fires only when the file is present at the workspace root;
    the worker is responsible for reading the current content rather than
    a snapshot stored in the brief.
    """
    if not workspace_dir:
        return ""
    try:
        if os.path.isfile(os.path.join(workspace_dir, "ARCHITECTURE.md")):
            return (
                "[This repo has an ARCHITECTURE.md at the root — read it before "
                "exploring the codebase. It maps where each component lives and "
                "how the pieces connect.]\n\n"
            )
    except OSError:
        pass
    return ""


def worker_memory_pointer(workspace_dir: "str | None") -> str:
    """Return a one-line dispatch-brief pointer at the repo's worker memory
    index when ``<workspace>/.devclaw/MEMORY.md`` exists, else "".

    Same contract as :func:`architecture_map_pointer`: pure probe, never
    raises, zero fact bodies — the worker reads the index itself (its
    standard instructions say how). A repo without the index renders a brief
    byte-identical to one that never had memory (spec 034 SC-004).
    """
    if not workspace_dir:
        return ""
    try:
        if os.path.isfile(os.path.join(workspace_dir, WORKER_MEMORY_INDEX)):
            return (
                "[This repo carries worker memory at .devclaw/MEMORY.md — read the "
                "index before starting and pull the fact files it lists when they "
                "bear on your task.]\n\n"
            )
    except OSError:
        pass
    return ""


def verification_environment(workspace_dir: "str | None") -> str:
    """Render the project's declared verification environment into the brief.

    The gap this closes is not a weak check, it is a check of a different
    thing. fs-431's verify command was
    ``dotnet test … --filter 'Category!=Integration'`` while the failing CI job
    was the integration tier: the gate excluded exactly what was red, so it
    returned PASSED with full confidence for nine days and ten increments.
    Three separate workers then wrote the sandbox's capability gaps into REPO
    NOTES independently — the fact arriving too late, in the wrong place, once
    per burned session.

    DECLARED, never derived. Devclaw does not read the verify command's flags:
    the day it parses ``--filter 'Category!=Integration'`` it owes the same for
    pytest, jest and go test, and encoding a project's tooling is what
    constitution IX forbids. The project states the gap in its own words and
    devclaw carries it, unread and unjudged.

    Read from the default-branch tip, never the worktree: the manifest is a
    gate-relevant declaration and the sandboxed worker can write to its own
    branch (the #358 class). Best-effort like its siblings — a malformed or
    unreadable manifest renders "" here rather than wedging a dispatch; the
    loud path for that already exists at strictness resolution.
    """
    if not workspace_dir:
        return ""
    try:
        from ..project_manifest import load_manifest_at_base

        manifest = load_manifest_at_base(workspace_dir)
    except Exception:  # noqa: BLE001 — a pointer never wedges a dispatch
        return ""
    env = getattr(manifest, "environment", None)
    if env is None:
        return ""
    lines: list[str] = []
    if env.image:
        lines.append(f"runs in {env.image}")
    if env.services:
        lines.append(f"with {', '.join(env.services)}")
    if env.tools:
        lines.append(f"and {', '.join(env.tools)}")
    parts: list[str] = []
    if lines:
        parts.append(
            "[This project's CI verifies in a declared environment: "
            + " ".join(lines)
            + ". Your sandbox is not that environment — check before assuming a "
            "surface is available, and report a real gap as BLOCKED: env rather "
            "than working around it.]"
        )
    if env.verify_excludes:
        parts.append(
            "[Your verify command does NOT cover: "
            + "; ".join(env.verify_excludes)
            + ". A PASS from it is a pass on a strict subset of what CI runs, "
            "never agreement with CI — CI is the verdict of record.]"
        )
    return ("\n\n".join(parts) + "\n\n") if parts else ""
