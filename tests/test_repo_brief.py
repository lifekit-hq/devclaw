"""Structural regression tests for the repo-brief pointers (spec 029 + 034).

One invariant per pointer, no store or engine involved (pure file-system
probes, deterministic, zero LLM):

  1. The architecture map pointer fires when ARCHITECTURE.md is present and
     is absent when the file is not.
  2. The worker memory pointer fires when ``.devclaw/MEMORY.md`` is present
     and is absent when it is not — so a repo that never adopted memory
     renders a brief byte-identical to one that never had it (spec 034
     SC-004), and the brief carries a pointer, never fact bodies (FR-002).
"""

from devclaw.goal.repo_brief import (
    architecture_map_pointer,
    worker_memory_pointer,
)


# ---------------------------------------------------------------------------
# spec 029 — architecture map pointer
# ---------------------------------------------------------------------------


def test_dispatch_includes_architecture_pointer_when_map_exists(tmp_path):
    """When ARCHITECTURE.md is present the pointer must appear in the prefix."""
    arch = tmp_path / "ARCHITECTURE.md"
    arch.write_text("# Architecture\n\nMap content here.\n", encoding="utf-8")

    result = architecture_map_pointer(str(tmp_path))

    assert "ARCHITECTURE.md" in result
    assert "read it before" in result.lower() or "read it" in result.lower()
    assert result.strip()  # non-empty


def test_dispatch_skips_architecture_pointer_when_no_map(tmp_path):
    """When ARCHITECTURE.md is absent the pointer must be empty — no noise, no
    broken link."""
    # tmp_path is an empty directory with no ARCHITECTURE.md
    assert not (tmp_path / "ARCHITECTURE.md").exists()

    result = architecture_map_pointer(str(tmp_path))

    assert result == ""


def test_architecture_pointer_returns_empty_for_none_workspace():
    """None workspace_dir is a valid call (goal has no workspace); must return
    '' silently, never raise."""
    assert architecture_map_pointer(None) == ""


def test_architecture_pointer_returns_empty_for_missing_workspace():
    """Non-existent workspace dir: best-effort, returns '' without raising."""
    result = architecture_map_pointer("/nonexistent/path/to/workspace")
    assert result == ""


# ---------------------------------------------------------------------------
# spec 034 — worker memory pointer (the brief never carries fact bodies)
# ---------------------------------------------------------------------------


def test_memory_pointer_present_iff_index_exists(tmp_path):
    """The pointer fires on ``.devclaw/MEMORY.md`` alone, names the index, and
    carries none of the fact text — the brief's size is independent of how
    much memory the repo holds. Absent index ⇒ '' (byte-identical brief)."""
    assert worker_memory_pointer(str(tmp_path)) == ""
    assert worker_memory_pointer(None) == ""
    assert worker_memory_pointer("/nonexistent/path/to/workspace") == ""

    mem = tmp_path / ".devclaw" / "memory"
    mem.mkdir(parents=True)
    (mem / "private-tmpdir.md").write_text(
        "# Private TMPDIR\n\nSECRET-FACT-BODY: run pytest with TMPDIR=$(mktemp -d).\n"
    )
    (tmp_path / ".devclaw" / "MEMORY.md").write_text(
        "- [Private TMPDIR](memory/private-tmpdir.md) — pytest needs a private tmpdir\n"
    )

    result = worker_memory_pointer(str(tmp_path))

    assert ".devclaw/MEMORY.md" in result
    assert "SECRET-FACT-BODY" not in result and "private-tmpdir" not in result
    assert result == worker_memory_pointer(str(tmp_path))  # deterministic
