"""Merge-on-close (spec 025 US1): an achieved goal closes MERGED or it does
not close — the one seam where devclaw merges a PR.

The conftest `_no_real_merges_by_default` fixture stubs the seam to NO_PR for
the rest of the suite; every test here patches `tick_donegate._attempt_merge`
with a scripted fake and asserts the close/heal/park state machine around it.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from devclaw.goal import merge_on_close as moc
from devclaw.goal import tick_donegate
from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)

ACHIEVED = json.dumps({
    "verdict": "achieved",
    "rationale": "/health exists and is tested",
    "clauses": [
        {"clause": "/health returns 200", "satisfied": True,
         "evidence": "src/Health.cs:12 returns OK; HealthTests.cs:8 asserts 200"},
    ],
})

#: spec 035: the first close round pins the rubric (id c1 for the one clause
#: above), so a SECOND close round of the same contract revision must judge
#: by pinned id — the decomposition-shape response would fail closed.
ACHIEVED_PINNED = json.dumps({
    "verdict": "achieved",
    "rationale": "/health exists and is tested",
    "clauses": [
        {"id": "c1", "satisfied": True,
         "evidence": "src/Health.cs:12 returns OK; HealthTests.cs:8 asserts 200"},
    ],
})

PR_URL = "https://github.com/o/r/pull/7"


class ScriptedMerge:
    """Scripted `attempt_merge` double: pops one result per call (the last
    result repeats), records the branch each call targeted."""

    def __init__(self, *results: moc.MergeResult):
        self.results = list(results)
        self.branches: list[str] = []

    async def __call__(self, workspace_dir: str, branch: str) -> moc.MergeResult:
        self.branches.append(branch)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def _store(tmp_path):
    return GoalStore(tmp_path, now=Clock())


def _verifying_status(base: "GoalStatus | None" = None) -> GoalStatus:
    s = base if base is not None else GoalStatus()
    return replace(
        s, phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify",
                           is_done_check=True),
    )


async def _tick(store, goal_id, evaluator, engine, notifier, fetcher=None):
    return await tick_goal(
        goal_id, store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True, issue_fetcher=fetcher,
    )


@pytest.mark.asyncio
async def test_achieved_close_squash_merges_the_cumulative_pr_before_done(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())

    phase_at_merge_time: list[str] = []

    async def merge_and_snapshot(workspace_dir, branch):
        # the ordering assertion: the merge fires BEFORE the ACHIEVE transition
        phase_at_merge_time.append(store.load_status("g").phase)
        return moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL,
                               merged_sha="abc123def456", detail="squash-merged")

    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge_and_snapshot)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="report"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, notifier)

    assert out is Outcome.DONE
    # called exactly once, BEFORE the ACHIEVE transition: the polling done-gate
    # has already settled verifying → idle (DONE_GATE_SETTLED) when the
    # resolution runs, so "before the close" reads as idle — never "done".
    assert phase_at_merge_time == ["idle"]
    s = store.load_status("g")
    assert s.phase == "done"
    assert s.pending_merge_pr == "" and s.merge_heal_attempted is False
    assert any("merged abc123def456" in m for m in notifier.sent)


class _GreenOnHead:
    """A remote checker answering ``passing`` for one fixed PR head."""

    def __init__(self, head_sha: str):
        from devclaw.goal.remote_checks import RemoteChecksResult
        self.result = RemoteChecksResult("passing", "all green", head_sha=head_sha)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, repo_url: str, branch: str):
        self.calls.append((repo_url, branch))
        return self.result


@pytest.mark.asyncio
async def test_a_head_moved_after_the_green_read_never_merges(tmp_path, monkeypatch):
    """Spec 032 US1 / FR-002: merge-on-close requires the SAME head whose CI
    was read green when the gate opened. A head that moved in between (a hand
    push, a new increment) re-holds the goal on ``mechanical:ci`` and hands the
    proposal back to the gate — the achieved verdict was for another head."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", replace(_verifying_status(), ci_green_head="old0000aaaa"))
    fake = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL,
                                         merged_sha="deadbeef", detail="squash-merged"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", fake)
    checker = _GreenOnHead("new1111bbbb")
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="report"))
    notifier = RecordingNotifier()

    out = await tick_goal(
        "g", store=store, engine=engine, evaluator_caller=FakeClaude(ACHIEVED),
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True, remote_checker=checker,
    )

    assert out is Outcome.BLOCKED
    assert fake.branches == []                      # the merge never fired
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "mechanical:ci"
    assert s.pending_done_proposal is True and s.ci_green_head == ""
    assert "moved" in (s.blocked_on or "")
    assert any("merge-on-close deferred" in line for line in store.recent_log("g").splitlines())


@pytest.mark.asyncio
async def test_merge_conflict_dispatches_one_resolution_increment_then_parks(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())
    fake = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.CONFLICT, pr_url=PR_URL,
                                         detail="not mergeable"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", fake)
    notifier = RecordingNotifier()

    # close attempt 1: CONFLICT with the heal budget available → back to idle
    # with the auto-conflict steering row, never a park
    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      notifier)
    assert out is Outcome.SLEPT
    s = store.load_status("g")
    assert s.phase == "idle" and s.merge_heal_attempted is True
    assert "[merge-conflict]" in store.unread_steering("g")

    # next tick: the resolution increment dispatches through the NORMAL
    # advance pipeline, brief carrying the conflict steering
    engine = FakeEngine()
    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, notifier)
    assert out is Outcome.DISPATCHED
    (action, _g, _u), = engine.dispatched
    assert "[merge-conflict]" in action.goal and PR_URL in action.goal

    # close attempt 2: CONFLICT again with the budget spent → park loudly.
    # Same contract revision ⇒ the rubric is pinned now; the verdict judges
    # by pinned id (spec 035).
    store.save_status("g", _verifying_status(store.load_status("g")))
    out = await _tick(store, "g", FakeClaude(ACHIEVED_PINNED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      notifier)
    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked"
    assert s.blocked_kind == "mechanical:merge_failed"
    assert s.pending_merge_pr == PR_URL
    assert len(fake.branches) == 2  # exactly two attempts, never a third heal
    assert any("🟥" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_already_merged_pr_at_close_is_success(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())
    monkeypatch.setattr(tick_donegate, "_attempt_merge", ScriptedMerge(
        moc.MergeResult(moc.MergeOutcome.ALREADY_MERGED, pr_url=PR_URL, merged_sha="fff000")))

    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      RecordingNotifier())

    assert out is Outcome.DONE
    assert store.load_status("g").phase == "done"


@pytest.mark.asyncio
async def test_closed_unmerged_pr_parks_loudly(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())
    monkeypatch.setattr(tick_donegate, "_attempt_merge", ScriptedMerge(
        moc.MergeResult(moc.MergeOutcome.CLOSED_UNMERGED, pr_url=PR_URL,
                        detail="PR closed without merge")))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      notifier)

    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "mechanical:merge_failed"
    assert s.pending_merge_pr == PR_URL
    assert any("closed_unmerged" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_resume_after_merge_failure_retries_merge_without_done_gate(tmp_path, monkeypatch):
    # post-resume state: idle, verdict already stood, only the merge is owed
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="idle", pending_merge_pr=PR_URL, merge_heal_attempted=True,
        last_eval_note="all clauses satisfied",
    ))
    fake = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL,
                                         merged_sha="abc123def456"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", fake)
    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, FakeEngine(), notifier)

    assert out is Outcome.DONE
    assert evaluator.calls == 0  # FR-003: the merge is retried, never the gate
    s = store.load_status("g")
    assert s.phase == "done" and s.pending_merge_pr == ""
    assert any("merge completed on retry" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_failed_retry_reparks_without_cognition(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="idle", pending_merge_pr=PR_URL, merge_heal_attempted=True,
    ))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", ScriptedMerge(
        moc.MergeResult(moc.MergeOutcome.CONFLICT, pr_url=PR_URL, detail="still conflicting")))
    evaluator = FakeClaude()

    out = await _tick(store, "g", evaluator, FakeEngine(), RecordingNotifier())

    assert out is Outcome.BLOCKED
    assert evaluator.calls == 0
    s = store.load_status("g")
    assert s.blocked_kind == "mechanical:merge_failed" and s.pending_merge_pr == PR_URL


@pytest.mark.asyncio
async def test_forge_error_at_close_parks_after_bounded_retries(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())
    monkeypatch.setattr(tick_donegate, "_attempt_merge", ScriptedMerge(
        moc.MergeResult(moc.MergeOutcome.ERROR, pr_url=PR_URL,
                        detail="gh pr merge failed: 502")))

    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      RecordingNotifier())

    assert out is Outcome.BLOCKED
    assert store.load_status("g").blocked_kind == "mechanical:merge_failed"


@pytest.mark.asyncio
async def test_merge_failure_never_wedges_other_goals(tmp_path, monkeypatch):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g", workspace_dir="/repos/alpha")
    seed_goal(tmp_path, "h", workspace_dir="/repos/beta")
    store.save_status("g", _verifying_status())
    store.save_status("h", GoalStatus(phase="idle", lifecycle="executing"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", ScriptedMerge(
        moc.MergeResult(moc.MergeOutcome.ERROR, detail="boom")))

    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      RecordingNotifier())
    assert out is Outcome.BLOCKED
    # the other project's goal still advances normally on the same heartbeat
    engine = FakeEngine()
    out = await _tick(store, "h", FakeClaude(ACHIEVED), engine, RecordingNotifier())
    assert out is Outcome.DISPATCHED


@pytest.mark.asyncio
async def test_no_pr_close_is_an_explicit_no_change_success(tmp_path):
    # the conftest default IS the NO_PR outcome — a review-only/no-change goal
    # closes normally with the honest no-PR note.
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", _verifying_status())
    notifier = RecordingNotifier()

    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      notifier)

    assert out is Outcome.DONE
    assert any("no PR to merge" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_merge_conflict_heal_survives_closed_referenced_issues(tmp_path, monkeypatch):
    """Tripwire (brake machinery): the ONE bounded conflict-resolution
    increment dispatches even when every referenced issue is closed.

    The heal returns the goal to idle with ``donegate_rounds`` reset to 0 —
    exactly the state the dispatch-boundary freshness guard's "all issues
    closed → propose done without a worker" shortcut keys on. It took that
    shortcut, the increment never ran, and the second CONFLICT parked the
    goal with the heal budget spent but never used (issue-443, 2026-09-03).
    """
    from devclaw.goal.issue_ref import IssueSnapshot
    from tests.goal_fakes import FakeIssueFetcher

    store = _store(tmp_path)
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    store.save_status("g", _verifying_status())
    fake = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.CONFLICT, pr_url=PR_URL,
                                         detail="not mergeable"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", fake)
    notifier = RecordingNotifier()
    closed = FakeIssueFetcher({7: IssueSnapshot(
        number=7, title="t", body="ctx\n## Acceptance\n- /health returns 200",
        state="closed")})

    # close attempt 1: CONFLICT with the budget available → idle + heal owed
    out = await _tick(store, "g", FakeClaude(ACHIEVED),
                      FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
                      notifier, closed)
    assert out is Outcome.SLEPT
    assert store.load_status("g").merge_heal_attempted is True

    # next tick: every referenced issue is closed — the guard must still
    # dispatch the resolution increment, never re-propose done into the
    # same conflict
    engine = FakeEngine()
    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, notifier, closed)
    assert out is Outcome.DISPATCHED
    (action, _g, _u), = engine.dispatched
    assert "[merge-conflict]" in action.goal and PR_URL in action.goal
    assert len(fake.branches) == 1  # no second merge attempt before the increment
