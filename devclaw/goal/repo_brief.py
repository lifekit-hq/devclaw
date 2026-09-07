"""Repo-scoped pointers for the dispatch brief (spec 029 + spec 034).

The worker PULLS its repo context from the checkout; the host never pushes
it. This module holds the two pure file-existence probes the dispatch path
prepends to the brief so the worker knows what to read first:

- :func:`architecture_map_pointer` — the repo's ``ARCHITECTURE.md`` (spec 029);
- :func:`worker_memory_pointer` — the repo's committed ``.devclaw/MEMORY.md``
  memory index (spec 034): one fact per file under ``.devclaw/memory/``,
  maintained by the worker inside its increments and reviewed like code.

Both are best-effort, never-raise, zero-LLM, and independent of how much the
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
