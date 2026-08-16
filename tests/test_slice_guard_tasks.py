"""The slice-guard's DETECTION half, rewired onto speckit ``tasks.md`` (spec 008
US1, FR-005 / SC-003).

The build-ahead UNIT is the STORY-SLICE (``[US<n>]``), not the raw checkbox: a
speckit ``tasks.md`` is fine-grained (one story is many ``T00x`` rows), so
closing five tasks of ONE story is one reviewable slice, and only advancing >1
distinct ``(feature, story)`` slice is building ahead. ``tasks_flips_sync``
counts distinct slices advanced between ``HEAD^`` and ``HEAD`` across
``specs/*/tasks.md``; it NEVER reads ``PLAN.md`` when a ``tasks.md`` exists,
falls back to the legacy ``PLAN.md`` reader only when no ``tasks.md`` exists
anywhere (D4), and is best-effort / fail-OPEN on detection (a git hiccup / no
parent commit ⇒ 0). The VERDICT half (advise under trust / block under strict)
is unchanged and covered in test_goal_tick.py.
"""

from __future__ import annotations

import os
import subprocess

from devclaw.goal import slice_guard as _slice_guard
from devclaw.goal.slice_guard import (
    count_slice_advances,
    current_feature_dir_sync,
    tasks_flips_sync,
)

# A realistic speckit tasks.md: US1 spans T001+T002, US2 is T003; T000 is an
# untagged setup task (no [US<n>] — rides whatever story ships).
_TASKS = """# Tasks: Some Feature

- [ ] T000 scaffold the repo skeleton
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


# ---- the pure story-slice counter -----------------------------------------


def test_one_story_slice_is_one_advance_regardless_of_task_count():
    # Closing BOTH US1 tasks (T001+T002) is ONE reviewable slice — the whole
    # point of the re-model. The old checkbox counter called this 2 (a false
    # build-ahead that blocked every well-sliced increment under strict).
    after = _flip(
        _TASKS,
        "T001 [P] [US1] scaffold the module",
        "T002 [US1] wire the endpoint",
    )
    assert count_slice_advances(_TASKS, after) == 1


def test_advancing_two_stories_is_the_buildahead_signal():
    after = _flip(
        _TASKS,
        "T001 [P] [US1] scaffold the module",
        "T003 [P] [US2] add the second story",
    )
    assert count_slice_advances(_TASKS, after) == 2


def test_untagged_setup_task_rides_a_story_never_counts_alone():
    # T000 has no [US<n>] tag; closing it alongside US1 is still one slice.
    after = _flip(
        _TASKS,
        "T000 scaffold the repo skeleton",
        "T001 [P] [US1] scaffold the module",
    )
    assert count_slice_advances(_TASKS, after) == 1


def test_reworded_task_keyed_by_id_still_counts():
    # The task text is edited in the same commit it is checked — a label-keyed
    # counter would drop this flip (before/after labels differ). Keyed by T-id.
    before = "- [ ] T002 [US1] wire the endpoint\n"
    after = "- [x] T002 [US1] wire the endpoint via /v2 with retries\n"
    assert count_slice_advances(before, after) == 1


def test_brand_new_tasks_md_counts_each_completed_story():
    # A feature created AND its stories checked in ONE increment (before == "",
    # the file did not exist at HEAD^). This is the Ledger mega-PR class the
    # guard exists to catch — the old counter scored it 0 (every box was
    # "newly-added-already-checked").
    after = _flip(
        _TASKS,
        "T001 [P] [US1] scaffold the module",
        "T002 [US1] wire the endpoint",
        "T003 [P] [US2] add the second story",
    )
    assert count_slice_advances("", after) == 2  # US1 + US2


def test_brand_new_tasks_md_one_story_is_one_slice():
    # A new feature that ships ONE story is a legit first slice, not build-ahead.
    after = _flip(
        _TASKS,
        "T001 [P] [US1] scaffold the module",
        "T002 [US1] wire the endpoint",
    )
    assert count_slice_advances("", after) == 1


def test_adding_and_completing_a_new_story_in_one_increment_is_an_advance():
    # US1/US2 pre-existed unchecked; this increment ADDS a brand-new US3 and
    # completes it in the same commit — the story went nonexistent→done in one
    # increment, which IS advancing it (this is what lets the new-tasks.md
    # mega-PR class be caught; the old milestone counter scored it 0).
    after = _TASKS + "- [x] T004 [US3] a brand new done item\n"
    assert count_slice_advances(_TASKS, after) == 1


def test_no_story_tags_anywhere_collapses_to_one_bucket():
    # A tasks.md with no [US<n>] tags at all: we can't resolve stories, so any
    # advance is a single unit — never over-trips.
    before = "- [ ] T001 do a thing\n- [ ] T002 do another\n"
    after = "- [x] T001 do a thing\n- [x] T002 do another\n"
    assert count_slice_advances(before, after) == 1


def test_count_slice_advances_fails_open_to_zero_on_empty():
    assert count_slice_advances("", "") == 0
    assert count_slice_advances("just prose", "still just prose") == 0


# ---- the git wrapper: reads tasks.md, never PLAN.md ------------------------


def test_tasks_flips_sync_counts_slices_from_tasks_md_not_plan(tmp_path, monkeypatch):
    # PLAN.md is NEVER read when a tasks.md exists — prove it by making the
    # legacy PLAN.md reader BLOW UP if consulted (assert absence).
    def _never(_ws):  # pragma: no cover - must not be called
        raise AssertionError("PLAN.md reader consulted while tasks.md exists")

    monkeypatch.setattr(_slice_guard, "mega_dump_flips_sync", _never)

    repo = _repo(tmp_path)
    specs = repo / "specs" / "001-some-feature"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(_TASKS)
    (repo / "PLAN.md").write_text(_PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed feature + plan")

    # This increment closes BOTH US1 tasks (ONE slice) and both PLAN.md
    # milestones. The guard must report the TASKS.md slice count (1), not 2.
    (specs / "tasks.md").write_text(
        _flip(
            _TASKS,
            "T001 [P] [US1] scaffold the module",
            "T002 [US1] wire the endpoint",
        )
    )
    (repo / "PLAN.md").write_text(_flip(_PLAN, "scaffold the app", "add auth"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "close US1")

    assert tasks_flips_sync(str(repo)) == 1


def test_tasks_flips_sync_trips_on_two_stories_in_one_increment(tmp_path):
    repo = _repo(tmp_path)
    specs = repo / "specs" / "001-a"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(_TASKS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    (specs / "tasks.md").write_text(
        _flip(
            _TASKS,
            "T001 [P] [US1] scaffold the module",
            "T003 [P] [US2] add the second story",
        )
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "build ahead into US2")
    assert tasks_flips_sync(str(repo)) == 2


def test_tasks_flips_sync_new_feature_multiple_stories_is_caught(tmp_path):
    # The mega-PR class: a feature created AND multiple stories closed in one
    # commit (tasks.md absent at HEAD^). Must trip (>1), not sail through as 0.
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    specs = repo / "specs" / "007-mega"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(
        _flip(
            _TASKS,
            "T001 [P] [US1] scaffold the module",
            "T002 [US1] wire the endpoint",
            "T003 [P] [US2] add the second story",
        )
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "whole feature in one commit")
    assert tasks_flips_sync(str(repo)) == 2  # US1 + US2


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
    # US1 advances in feature 001, US2 in feature 002 — two DIFFERENT slices.
    (f1 / "tasks.md").write_text(_flip(_TASKS, "T001 [P] [US1] scaffold the module"))
    (f2 / "tasks.md").write_text(_flip(_TASKS, "T003 [P] [US2] add the second story"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one story each")
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


# ---- current_feature_dir_sync: the done-gate's grounding target ------------


def test_current_feature_dir_is_the_most_recently_worked_not_lexical_first(tmp_path):
    # An earlier feature (005) is still incomplete but STALE; the active feature
    # (012) was touched most recently. The old lexical-first pick returned 005 and
    # grounded the done-gate on the wrong spec.md; most-recent-mtime returns 012.
    repo = tmp_path / "repo"
    (repo / "specs" / "005-stale").mkdir(parents=True)
    (repo / "specs" / "012-active").mkdir(parents=True)
    (repo / "specs" / "005-stale" / "tasks.md").write_text(_TASKS)  # written first
    os.utime(repo / "specs" / "005-stale" / "tasks.md", (1_000, 1_000))
    (repo / "specs" / "012-active" / "tasks.md").write_text(_TASKS)  # newer
    os.utime(repo / "specs" / "012-active" / "tasks.md", (2_000, 2_000))
    assert current_feature_dir_sync(str(repo)) == "specs/012-active"


def test_current_feature_dir_empty_when_no_specs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert current_feature_dir_sync(str(repo)) == ""
    assert current_feature_dir_sync(str(tmp_path / "nope")) == ""
