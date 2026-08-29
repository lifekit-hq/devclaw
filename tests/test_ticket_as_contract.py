"""Spec 024 — the ticket is the contract's one home.

US1's core (done_when="" + live scenario contract + slots declared empty)
shipped with spec 022's dispatch_issue; these pins cover 024's own deltas:
the issue-backed create lane needs NO slot arguments (the template sections
are the authoring home), the prose lane keeps spec 012's rejection, and the
repo's issue template actually carries the sections grading and briefs read.
"""

from __future__ import annotations

from pathlib import Path

from devclaw.goal.admission import verify_goal


def test_issue_backed_goal_needs_no_slot_arguments():
    """Spec 024 US2: with issue refs, omitted saga slots are 'authored on the
    ticket', not unfilled — admission passes with zero slot arguments."""
    result = verify_goal(
        objective="Work issue #7: fix the widget",
        workspace_dir="/repos/demo",
        done_when="",                      # live scenario contract (spec 019)
        backlog=[],
        repo_url="https://github.com/org/repo.git",
        spec="",
        out_of_scope=None, invariants=None, established=None,
        has_issue_refs=True,
    )
    assert result.admitted, [c.code for c in result.conditions]


def test_prose_goal_still_rejects_unfilled_slots():
    """The issue-less lane keeps spec 012's authored-slot rejection byte-for-
    byte (spec 024 FR-004) — the exemption is scoped to the ticket lane."""
    result = verify_goal(
        objective="Build the bench API end to end with real tests",
        workspace_dir="/repos/demo",
        done_when="every endpoint has a passing integration test in CI",
        backlog=[],
        repo_url="https://github.com/org/repo.git",
        spec="",
        out_of_scope=None, invariants=None, established=None,
        has_issue_refs=False,
    )
    assert not result.admitted
    codes = {c.code for c in result.conditions}
    assert {"missing_out_of_scope", "missing_invariants", "missing_established"} <= codes


def test_issue_template_carries_the_saga_sections():
    """Spec 024 US2/FR-003: the template is the sections' authoring home —
    Acceptance (the live contract) + the three saga sections."""
    tmpl = (Path(__file__).resolve().parents[1]
            / ".github" / "ISSUE_TEMPLATE" / "devclaw-work.md").read_text()
    for section in ("## What", "## Acceptance", "## Out of scope",
                    "## Invariants", "## Established"):
        assert section in tmpl, f"template lost its {section} section"
