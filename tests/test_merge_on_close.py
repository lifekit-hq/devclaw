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


@pytest.mark.asyncio
async def test_closed_contract_with_a_refusing_gate_asks_instead_of_grinding(tmp_path, monkeypatch):
    """Tripwire (brake machinery): all referenced issues closed AND the
    done-gate already refused ⇒ raise a Problem and BLOCK, never dispatch.

    A pointer goal reads done_when live from its issues (spec 019). Once every
    issue is closed, no dispatch can amend the contract — but the gate keeps
    judging the pinned revision, so the loop re-dispatches forever. fs-431
    burned 8 rounds and fs-421 five that way, logging "dropped from the
    remaining scope" and "dispatching worker to complete the remaining
    contract" back to back every time
    (specs/tiny/closed-contract-raises-a-problem).
    """
    from devclaw.goal.issue_ref import IssueSnapshot
    from tests.goal_fakes import FakeIssueFetcher

    store = _store(tmp_path)
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    # the gate has refused before, and no merge heal is owed
    store.save_status("g", replace(
        store.load_status("g"), phase="idle", donegate_rounds=2,
        last_eval_note="clause c2 (price-hike) is structurally unreachable",
    ))
    notifier = RecordingNotifier()
    closed = FakeIssueFetcher({7: IssueSnapshot(
        number=7, title="t", body="ctx\n## Acceptance\n- /health returns 200",
        state="closed")})

    engine = FakeEngine()
    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, notifier, closed)

    assert out is Outcome.BLOCKED
    assert engine.dispatched == [], "a vanished contract must not be re-dispatched"
    saved = store.load_status("g")
    assert saved.phase == "blocked" and saved.blocked_kind == "needs_answer"
    assert saved.problem_id, "a human-gated block carries a typed Problem"

    prob = store.current_problem("g")
    assert prob.status == "open"
    # the owner is told WHICH issue vanished and WHY the gate refuses
    assert "#7" in prob.what and "refused 2 round(s)" in prob.what
    assert "price-hike" in prob.clause
    # the owner's own closure is the presumption, but it never fires silently
    assert prob.default_key == "accept_close"
    assert {o.key for o in prob.options} == {"accept_close", "correct", "cancel"}
    assert any("[g]" in m for m in notifier.sent)


# ---- spec 041 US1: an owner Decision is executed by the next tick -----------
# Tripwire classes: zero-token (an accepted close spends no evaluator call) and
# brake machinery (a pending correction beats the closed-issue shortcut).

def _owner_decision(store, goal_id, *, option="", text="", verb="decide", clause="c2", made_at=None):
    """Write the Decision row resolve_problem would write — no Problem needed
    for the tick's reading of it (it derives from goal_decisions alone)."""
    from devclaw.goal.models import Decision
    from devclaw.state_store import _now_ms

    d = Decision(
        id=f"dec_{option or verb}_{made_at or 0}", goal_id=goal_id, problem_id="",
        clause=clause, verb=verb, option_key=option, text=text,
        provenance="owner", made_by="denys", made_at=made_at or (_now_ms() + 60_000),
    )
    store.record_decision(d, problem_status="resolved")
    return d


@pytest.mark.asyncio
async def test_owner_accept_close_closes_and_merges_without_an_evaluator_call(tmp_path, monkeypatch):
    """Spec 041 FR-003: the owner's accept_close IS the verdict. The next tick
    merges and closes on the mechanical facts — zero cognition, no gate
    round — and the accepted gap rides the close as a follow-up. Before this,
    fs-318/421/429 cycled decide → gate → strict downgrade → Problem → decide
    (3 decisions, 4 steers, 4 review calls, still open on 2026-09-08)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    store.set_strictness("g", "strict")
    store.save_status("g", replace(store.load_status("g"), phase="idle", donegate_rounds=1,
                                   last_plan_at=store.now_iso()))
    _owner_decision(store, "g", option="accept_close", clause="structural: shape concerns")
    merge = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL, merged_sha="abc123def456"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge)
    evaluator, engine, notifier = FakeClaude(ACHIEVED), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DONE
    assert evaluator.calls == 0, "an accepted close never re-asks the evaluator"
    assert engine.dispatched == []
    s = store.load_status("g")
    assert s.phase == "done" and "accept_close" in s.next
    log = store.recent_log("g", 40)
    assert "closing without a gate round" in log
    assert "accepted by decision" in log  # the gap is a follow-up, never silent
    assert any("✅ [g]" in m and "accept_close" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_owner_accept_close_closes_while_another_goal_holds_the_lane(tmp_path, monkeypatch):
    """One definition of runnable (tinyspec ``one-definition-of-runnable``):
    an accepted close is a lane-FREE move — a remote squash-merge and a
    status flip, no checkout — so the project lane must never queue it.
    FR-003 says the close runs "before any cadence or work gate"; the lane
    gate is one of those. On 2026-09-08 fs-318/421/429 carried the owner's
    accept_close for 3–7 h behind a busy finance-sentry lane while the spec
    that made the Decision execute was already live."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    seed_goal(tmp_path, "holder", issue_refs=[8], done_when="")
    store.set_strictness("g", "strict")
    store.save_status("g", replace(store.load_status("g"), phase="idle", donegate_rounds=1,
                                   last_plan_at=store.now_iso()))
    # a successor mid-task on the same project: in-flight work always holds
    store.save_status("holder", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t9", "task", "advance the goal"),
    ))
    _owner_decision(store, "g", option="accept_close", clause="structural: shape concerns")
    merge = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL, merged_sha="abc123def456"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge)
    evaluator, engine, notifier = FakeClaude(ACHIEVED), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DONE, "a lane-free close never reads QUEUED"
    assert evaluator.calls == 0
    assert engine.dispatched == []
    assert store.load_status("g").phase == "done"
    assert store.load_status("holder").phase == "in_flight", "the holder is untouched"


@pytest.mark.asyncio
async def test_defaulted_accept_close_still_goes_through_the_gate(tmp_path, monkeypatch):
    """A timebox is not an owner ruling (spec 031 Q2 → C stands): a DEFAULTED
    accept_close never closes without the gate — the accepted-close rule is
    owner-provenance only."""
    from devclaw.goal.models import Decision
    from devclaw.state_store import _now_ms

    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", replace(store.load_status("g"), phase="idle", last_plan_at=store.now_iso()))
    store.record_decision(Decision(
        id="dec_defaulted", goal_id="g", problem_id="", clause="", verb="decide",
        option_key="accept_close", provenance="defaulted", made_by="tick", made_at=_now_ms() + 60_000,
    ), problem_status="defaulted")
    merge = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL, merged_sha="abc"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge)
    engine = FakeEngine()

    await _tick(store, "g", FakeClaude(ACHIEVED), engine, RecordingNotifier())

    assert store.load_status("g").phase != "done"
    assert engine.dispatched, "a pending defaulted decision is work: the tick dispatches, it never closes on its own"


@pytest.mark.asyncio
async def test_closed_contract_with_a_pending_correction_dispatches_instead_of_proposing(tmp_path):
    """Spec 041 FR-001/FR-002 (the fs-431 loop of 2026-09-08): with every
    referenced issue closed and a recorded correct_implementation the tick
    DISPATCHES the worker carrying the Decision — it does not take the
    "propose done without a worker" shortcut (eight worker-less rounds), and
    it does not raise a second Problem."""
    from devclaw.goal.issue_ref import IssueSnapshot
    from tests.goal_fakes import FakeIssueFetcher

    store = _store(tmp_path)
    seed_goal(tmp_path, "g", issue_refs=[7], done_when="")
    # resolve_problem's unblock shape: idle, rounds reset, plan instant in the past
    store.save_status("g", replace(store.load_status("g"), phase="idle", donegate_rounds=0,
                                   last_plan_at=store.now_iso()))
    _owner_decision(store, "g", verb="correct_implementation",
                    text="register the job through IBackgroundJobClient", clause="c2")
    closed = FakeIssueFetcher({7: IssueSnapshot(
        number=7, title="t", body="ctx\n## Acceptance\n- /health returns 200", state="closed")})
    evaluator, engine, notifier = FakeClaude(ACHIEVED), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, closed)

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1
    assert evaluator.calls == 0
    brief = engine.dispatched[0][0].goal
    assert "IBackgroundJobClient" in brief, "the Decision rides the brief as settled fact"
    assert store.load_status("g").problem_id == ""
    assert "there is work to dispatch (a recorded decision)" in store.recent_log("g", 20)


class _Reader:
    """A remote checker answering one canned ``RemoteChecksResult``."""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, repo_url: str, branch: str):
        self.calls.append((repo_url, branch))
        return self.result


def _conflicting_read():
    from devclaw.goal.remote_checks import RemoteChecksResult
    return RemoteChecksResult(
        "conflicting", "the PR conflicts with its base — its checks cannot run",
        head_sha="5138bf1dcc", pr_url=PR_URL,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("heal_spent", [False, True])
async def test_a_conflicting_pr_at_the_accepted_close_is_routed_to_the_conflict_heal_never_the_ci_hold(
    tmp_path, monkeypatch, heal_spent,
):
    """Spec 045 US2: GitHub creates no merge ref for a CONFLICTING PR, so its
    ``pull_request`` checks never report — on fs-431 (2026-09-10) the
    accepted close held ``mechanical:ci`` for 16 windows on three required
    checks that could not exist, then parked for the owner, while the settle
    path had read CONFLICTING two seconds before the hold. The CI reader now
    carries mergeability, and a ``conflicting`` read IS the merge-conflict
    outcome of spec 025: the bounded resolution increment when unspent, the
    ``mechanical:merge_failed`` park with its Problem when spent — on that
    tick, never a wait, nothing merged, zero cognition."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.set_strictness("g", "strict")
    store.save_status("g", replace(store.load_status("g"), phase="idle", donegate_rounds=1,
                                   last_plan_at=store.now_iso(), merge_heal_attempted=heal_spent))
    _owner_decision(store, "g", option="accept_close", clause="structural: shape concerns")
    merge = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL, merged_sha="abc123def456"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge)
    evaluator, engine, notifier = FakeClaude(ACHIEVED), FakeEngine(), RecordingNotifier()
    reader = _Reader(_conflicting_read())

    async def tick():
        return await tick_goal(
            "g", store=store, engine=engine, evaluator_caller=evaluator,
            notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
            verify_done=True, remote_checker=reader,
        )

    out = await tick()

    assert evaluator.calls == 0 and engine.dispatched == []
    assert merge.branches == [], "nothing merges on a conflicting read"
    s = store.load_status("g")
    assert s.blocked_kind != "mechanical:ci"
    assert "ci recheck" not in store.recent_log("g", 40)
    if not heal_spent:
        assert out is Outcome.SLEPT
        assert s.phase == "idle" and s.merge_heal_attempted is True
        steering = store.unread_steering("g")
        assert "[merge-conflict]" in steering and "default branch's side" in steering
        # the resolution increment dispatches through the normal pipeline
        out = await tick()
        assert out is Outcome.DISPATCHED
        (action, _g, _u), = engine.dispatched
        assert "[merge-conflict]" in action.goal and PR_URL in action.goal
    else:
        assert out is Outcome.BLOCKED
        assert s.phase == "blocked" and s.blocked_kind == "mechanical:merge_failed"
        assert s.pending_merge_pr == PR_URL
        assert any("🟥" in m for m in notifier.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("source, closes", [("auto-eval", True), ("denys", False), ("auto-ci", False)])
async def test_owner_accept_close_outranks_the_evaluators_own_concerns_but_not_a_human_or_a_fact(
    tmp_path, monkeypatch, source, closes,
):
    """Spec 045 US3: the evaluator's own unread concern rows are the gap the
    owner's ``accept_close`` accepted — the close consumes them as follow-ups
    and runs on that tick with no dispatch (on 2026-09-09 fs-431 ran a 3 h
    worker session on eight such rows before its accepted close ran). A
    human's later line and a mechanical correction still dispatch first —
    the last word and a fact both outrank the accept (spec 041)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.set_strictness("g", "strict")
    store.save_status("g", replace(store.load_status("g"), phase="idle", donegate_rounds=1,
                                   last_plan_at=store.now_iso()))
    _owner_decision(store, "g", option="accept_close", clause="structural: shape concerns")
    store.append_steering("g", ["[structural: concerns] Foo.cs:12 — extract the shared reader"], source=source)
    merge = ScriptedMerge(moc.MergeResult(moc.MergeOutcome.MERGED, pr_url=PR_URL, merged_sha="abc123def456"))
    monkeypatch.setattr(tick_donegate, "_attempt_merge", merge)
    evaluator, engine, notifier = FakeClaude(ACHIEVED), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert evaluator.calls == 0
    s = store.load_status("g")
    log = store.recent_log("g", 40)
    if closes:
        assert out is Outcome.DONE and engine.dispatched == []
        assert s.phase == "done"
        assert store.unread_steering("g") == "", "the accepted rows are consumed by the close"
        assert "accepted by decision" in log and "extract the shared reader" in log
    else:
        assert out is Outcome.DISPATCHED and s.phase != "done"
        (action, _g, _u), = engine.dispatched
        assert "extract the shared reader" in action.goal
