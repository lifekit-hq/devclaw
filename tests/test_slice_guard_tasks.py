"""The slice-guard's DETECTION half, rewired onto speckit ``tasks.md`` (spec 008
US1, FR-005 / SC-003).

``tasks_flips_sync`` reads the per-feature ``specs/*/tasks.md`` checkbox flips
(``- [ ]``→``- [x]``) between ``HEAD^`` and ``HEAD`` — the same build-ahead
signal the retired ``mega_dump_flips_sync`` derived from ``PLAN.md``, now sourced
from the speckit execution contract. It NEVER reads ``PLAN.md`` when a
``tasks.md`` exists (the substitution this arc is about), falls back to the
legacy ``PLAN.md`` reader only when no ``tasks.md`` exists anywhere (D4), and is
best-effort / fail-OPEN on detection (a git hiccup / no parent commit ⇒ 0). The
VERDICT half (advise under trust / block under strict) is unchanged and covered
in test_goal_tick.py.
"""

from __future__ import annotations

import subprocess

from devclaw.goal import slice_guard as _slice_guard
from devclaw.goal.slice_guard import count_checkbox_flips, tasks_flips_sync

# A realistic speckit tasks.md (the `- [ ] [ID] [P?] [Story]` convention).
_TASKS = """# Tasks: Some Feature

- [ ] T001 [P] [US1] scaffold the module
- [ ] T002 [US1] wire the endpoint
- [ ] T003 [P] [US2] add the second story
"""

_PLAN = """# PLAN.md

## Milestones
- [ ] scaffold the app
- [ ] add auth
"""


def _git(repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _flip(text: str, *labels: str) -> str:
    out = text
    for label in labels:
        out = out.replace(f"- [ ] {label}", f"- [x] {label}")
    return out


# ---- the pure checkbox counter --------------------------------------------


def test_count_checkbox_flips_counts_unchecked_to_checked():
    after = _flip(_TASKS, "T001 [P] [US1] scaffold the module")
    assert count_checkbox_flips(_TASKS, after) == 1


def test_count_checkbox_flips_counts_multiple_the_buildahead_signal():
    after = _flip(
        _TASKS,
        "T001 [P] [US1] scaffold the module",
        "T002 [US1] wire the endpoint",
    )
    assert count_checkbox_flips(_TASKS, after) == 2


def test_count_checkbox_flips_newly_added_already_checked_is_not_a_flip():
    after = _TASKS + "- [x] T004 [US3] a brand new done item\n"
    assert count_checkbox_flips(_TASKS, after) == 0


def test_count_checkbox_flips_fails_open_to_zero_on_empty():
    assert count_checkbox_flips("", "") == 0
    assert count_checkbox_flips("just prose", "still just prose") == 0


# ---- the git wrapper: reads tasks.md, never PLAN.md ------------------------


def test_tasks_flips_sync_counts_flips_from_tasks_md(tmp_path, monkeypatch):
    # PLAN.md is NEVER read when a tasks.md exists — prove it by making the
    # legacy PLAN.md reader BLOW UP if consulted (assert absence).
    def _never(_ws):  # pragma: no cover - must not be called
        raise AssertionError("PLAN.md reader consulted while tasks.md exists")

    monkeypatch.setattr(_slice_guard, "mega_dump_flips_sync", _never)

    repo = _repo(tmp_path)
    specs = repo / "specs" / "001-some-feature"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(_TASKS)
    # A PLAN.md ALSO present whose milestones flip — the guard must ignore it.
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed feature + plan")

    # This increment flips TWO tasks.md items (build-ahead) AND both PLAN.md
    # milestones. The guard must report the TASKS.md count (2), not PLAN's.
    (specs / "tasks.md").write_text(
        _flip(
            _TASKS,
            "T001 [P] [US1] scaffold the module",
            "T002 [US1] wire the endpoint",
        )
    )
    (repo / "PLAN.md").write_text(_flip(_PLAN, "scaffold the app", "add auth"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "build ahead")

    assert tasks_flips_sync(str(repo)) == 2


def test_tasks_flips_sync_sums_across_multiple_feature_dirs(tmp_path):
    repo = _repo(tmp_path)
    f1 = repo / "specs" / "001-a"
    f2 = repo / "specs" / "002-b"
    f1.mkdir(parents=True)
    f2.mkdir(parents=True)
    (f1 / "tasks.md").write_text(_TASKS)
    (f2 / "tasks.md").write_text(_TASKS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed two features")
    (f1 / "tasks.md").write_text(_flip(_TASKS, "T001 [P] [US1] scaffold the module"))
    (f2 / "tasks.md").write_text(_flip(_TASKS, "T003 [P] [US2] add the second story"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one flip in each")
    assert tasks_flips_sync(str(repo)) == 2


def test_tasks_flips_sync_falls_back_to_plan_md_when_no_tasks_md(tmp_path):
    # No specs/*/tasks.md anywhere ⇒ the LEGACY PLAN.md fallback (D4).
    repo = _repo(tmp_path)
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plan only")
    (repo / "PLAN.md").write_text(_flip(_PLAN, "scaffold the app", "add auth"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two milestones")
    assert tasks_flips_sync(str(repo)) == 2


def test_tasks_flips_sync_zero_when_neither_present(tmp_path):
    # Neither tasks.md nor PLAN.md ⇒ 0 (fail-OPEN on detection, as today).
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no plan, no tasks")
    (repo / "README.md").write_text("hi again\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "still none")
    assert tasks_flips_sync(str(repo)) == 0


def test_tasks_flips_sync_fails_open_on_non_repo_and_single_commit(tmp_path):
    assert tasks_flips_sync(str(tmp_path / "nope")) == 0
    repo = _repo(tmp_path)
    specs = repo / "specs" / "001-a"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(_flip(_TASKS, "T001 [P] [US1] scaffold the module"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "only commit")  # no HEAD^
    assert tasks_flips_sync(str(repo)) == 0
