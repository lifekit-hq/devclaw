"""The milestone-keyed mega-dump guardrail's DETECTION half (SDLC pipeline P2).

Pure ``count_milestone_flips`` parser (0 / 1 / >1 flips, tasks-not-counted,
unparseable ⇒ 0) plus the best-effort ``mega_dump_flips_sync`` git wrapper
(HEAD vs its parent, fail-OPEN on a missing PLAN.md / non-repo). The VERDICT
half (advise under trust / block under strict) lives in the settle path and is
covered in test_goal_tick.py.
"""

from __future__ import annotations

import subprocess

from devclaw.goal.slice_guard import count_milestone_flips, mega_dump_flips_sync

_PLAN = """# PLAN.md

## Destination
A thing that works.

## Milestones
- [ ] scaffold the app
- [ ] add auth
- [ ] add billing

## Tasks — scaffold the app
- [ ] create the project
- [ ] wire the DB
"""


def _flip(plan: str, *labels: str) -> str:
    """Return ``plan`` with each named milestone flipped ``[ ]`` → ``[x]``."""
    out = plan
    for label in labels:
        out = out.replace(f"- [ ] {label}", f"- [x] {label}")
    return out


# ---- the pure counter ------------------------------------------------------


def test_no_milestone_flip_counts_zero():
    assert count_milestone_flips(_PLAN, _PLAN) == 0


def test_one_milestone_flip_counts_one():
    after = _flip(_PLAN, "scaffold the app")
    assert count_milestone_flips(_PLAN, after) == 1


def test_two_milestone_flips_counts_two_the_megadump_signal():
    after = _flip(_PLAN, "scaffold the app", "add auth")
    assert count_milestone_flips(_PLAN, after) == 2


def test_task_checkboxes_outside_milestones_are_never_counted():
    # Flip BOTH task checkboxes under "## Tasks — …" but NO milestone. A task is
    # not a milestone: the count must stay 0 even though two boxes went [x].
    after = _flip(_PLAN, "create the project", "wire the DB")
    assert count_milestone_flips(_PLAN, after) == 0


def test_newly_added_already_checked_milestone_is_not_a_flip():
    # The plan GREW a milestone that is already checked (never seen unchecked in
    # `before`). That is not a [ ]→[x] flip — it must not count.
    after = _PLAN.replace(
        "- [ ] add billing", "- [ ] add billing\n- [x] add analytics"
    )
    assert count_milestone_flips(_PLAN, after) == 0


def test_absent_or_garbled_milestones_section_fails_open_to_zero():
    # No "## Milestones" section on either side ⇒ nothing to count ⇒ 0 (never
    # trips). Empty strings likewise.
    assert count_milestone_flips("just prose, no headers", "still just prose") == 0
    assert count_milestone_flips("", "") == 0
    # A milestone checked in `after` but the WHOLE section is missing in `before`
    # contributes nothing (fail toward 0).
    only_after = "## Milestones\n- [x] scaffold the app\n"
    assert count_milestone_flips("no plan yet", only_after) == 0


def test_section_ends_at_the_next_heading():
    # A checkbox that lives AFTER the Milestones section closes (under the next
    # ## heading) is out of scope even if it flips.
    before = "## Milestones\n- [ ] one\n## Later\n- [ ] two\n"
    after = "## Milestones\n- [ ] one\n## Later\n- [x] two\n"
    assert count_milestone_flips(before, after) == 0


# ---- the best-effort git wrapper -------------------------------------------


def _git(repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def test_mega_dump_flips_sync_counts_the_increments_milestone_flips(tmp_path):
    repo = _repo(tmp_path)
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plan")
    # The increment flips TWO milestones in one commit — a mega-dump.
    (repo / "PLAN.md").write_text(_flip(_PLAN, "scaffold the app", "add auth"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "build ahead")
    assert mega_dump_flips_sync(str(repo)) == 2


def test_mega_dump_flips_sync_one_milestone_is_a_clean_slice(tmp_path):
    repo = _repo(tmp_path)
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plan")
    (repo / "PLAN.md").write_text(_flip(_PLAN, "scaffold the app"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "clean slice")
    assert mega_dump_flips_sync(str(repo)) == 1


def test_mega_dump_flips_sync_fails_open_on_non_repo_and_missing_plan(tmp_path):
    # Not a git repo at all ⇒ 0 (best-effort, never raises).
    assert mega_dump_flips_sync(str(tmp_path / "nope")) == 0
    # A repo whose HEAD has no parent (single commit) ⇒ HEAD^ unresolvable ⇒ 0.
    repo = _repo(tmp_path)
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "only commit")
    assert mega_dump_flips_sync(str(repo)) == 0
