"""Structural regression tests for the repo-brief module (spec 027).

Guards two invariants:
  1. The architecture map pointer fires when ARCHITECTURE.md is present and
     is absent when the file is not — without any store or engine involvement
     (pure file-system probe, deterministic, zero LLM).
  2. The raised MAX_BRIEF_CHARS cap (12 000) prevents silent eviction of
     operational facts accumulated across a handful of goals.
"""

from devclaw.goal.repo_brief import (
    MAX_BRIEF_CHARS,
    architecture_map_pointer,
    merge_repo_notes,
)


# ---------------------------------------------------------------------------
# US1 — architecture map pointer
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
# US2 — brief retention under raised cap
# ---------------------------------------------------------------------------


def test_brief_retains_facts_under_raised_cap():
    """A brief that fits within the raised MAX_BRIEF_CHARS cap (12 000) must not
    lose any lines — the silent-eviction failure mode from the 4 000-char era."""
    # Build ~5 000 chars of existing brief content (well under the new cap)
    existing_lines = [
        f"fact-{i:03d}: some operational note about this repo — build quirk, test gotcha"
        for i in range(100)
    ]
    existing = "\n".join(existing_lines)
    assert len(existing) > 4_000  # would have been evicted under the old cap
    assert len(existing) < MAX_BRIEF_CHARS  # fits under the new cap

    new_notes = "new-fact: freshly discovered build quirk"
    merged = merge_repo_notes(existing, new_notes)

    # All original lines survive
    for line in existing_lines:
        assert line in merged, f"evicted: {line!r}"
    # New fact also present
    assert "freshly discovered build quirk" in merged


def test_max_brief_chars_is_at_least_12000():
    """Document the intentional raise: anyone lowering MAX_BRIEF_CHARS below
    12 000 must also update this test deliberately (spec 027)."""
    assert MAX_BRIEF_CHARS >= 12_000
