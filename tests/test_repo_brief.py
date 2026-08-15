"""Repo-scoped worker memory brief (mission-control borrow item 3).

goal_docs die with their goal and the sandbox workspace is git clean -fdx-
wiped per dispatch, so every new goal on the same repo relearned build
quirks from zero. These pin the host-side loop that fixes that:

- the pure merge policy (line dedupe + size cap, zero LLM);
- the project_docs row keyed by NORMALIZED workspace path (outlives goals);
- settle folds a worker's REPO NOTES hand-back into the brief, best-effort;
- the NEXT dispatch on the same workspace prepends the brief to the goal
  text (the thin advance brief now — the planner is gone, demolition P3b) —
  plain text injection, model-agnostic — while read-only reviews stay
  unseeded and idle ticks stay zero-token.
"""

from __future__ import annotations

import pytest

from devclaw.goal import repo_brief
from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, _advance_brief, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)


def _store(tmp_path):
    return GoalStore(tmp_path, now=Clock())


async def _tick(store, goal_id, engine, *, evaluator=None):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator or FakeClaude(),
        notifier=RecordingNotifier(), notify_url="http://relay",
        prepare_ws=fake_prepare, eval_every=99, verify_done=True,
    )


# ---- the advance brief binds "one increment" to the current milestone -------


def test_advance_brief_binds_the_increment_to_the_current_milestone(tmp_path):
    """SDLC pipeline (Part A) + spec 008 US1: the thin-advance brief must tell the
    worker to implement the smallest not-yet-done story-slice only and NOT build
    ahead — the prompt half of the build-ahead fix. Without this clause nothing
    bound "one increment" to one slice and the worker legally shipped the whole
    plan in one PR."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    brief = _advance_brief(store.load_effective_goal("g"), "")
    assert "story-slice" in brief.lower()
    assert "do not build ahead" in brief.lower()
    assert "one reviewable pr" in brief.lower()
    # The opening line — which `delivery._is_advance_brief` matches on as a prefix
    # ("Advance this goal by one substantive") — stays intact so title/branch
    # resolution still recognises the brief.
    assert brief.startswith("Advance this goal by one substantive")


# ---- the pure merge policy --------------------------------------------------


def test_merge_repo_notes_dedupes_and_appends():
    existing = "npm test needs NODE_OPTIONS=--max-old-space-size=4096"
    merged = repo_brief.merge_repo_notes(
        existing,
        "- npm test needs NODE_OPTIONS=--max-old-space-size=4096\n"
        "- e2e suite requires the dev server on :4200",
    )
    assert merged.splitlines() == [
        "npm test needs NODE_OPTIONS=--max-old-space-size=4096",
        "e2e suite requires the dev server on :4200",
    ]


def test_merge_repo_notes_skips_none_and_empty_lines():
    assert repo_brief.merge_repo_notes(None, "none") == ""
    assert repo_brief.merge_repo_notes("", "  \n- none\n") == ""


def test_merge_repo_notes_cap_drops_oldest_first():
    existing = "\n".join(f"old fact {i} " + "x" * 90 for i in range(50))
    merged = repo_brief.merge_repo_notes(existing, "the newest fact")
    assert len(merged) <= repo_brief.MAX_BRIEF_CHARS
    assert merged.splitlines()[-1] == "the newest fact"
    assert "old fact 0" not in merged  # oldest fell off, newest survived


def test_scope_key_normalizes_like_the_registry():
    assert repo_brief.scope_key_for("/repos/demo/") == "/repos/demo"
    assert repo_brief.scope_key_for("/repos//demo") == "/repos/demo"
    assert repo_brief.scope_key_for(None) is None
    assert repo_brief.scope_key_for("  ") is None


def test_render_brief_prefix_empty_for_blank_brief():
    assert repo_brief.render_brief_prefix(None) == ""
    assert repo_brief.render_brief_prefix("  \n ") == ""
    prefix = repo_brief.render_brief_prefix("fact one")
    assert "fact one" in prefix
    assert prefix.endswith("---\n\n")


# ---- the store row (project-scoped, outlives goals) -------------------------


def test_repo_brief_round_trips_and_outlives_goals(tmp_path):
    store = _store(tmp_path)
    assert store.read_repo_brief("/repos/demo") == ""
    store.write_repo_brief("/repos/demo", "fact")
    assert store.read_repo_brief("/repos/demo") == "fact"
    # Keyed by workspace, not goal — a different scope reads empty.
    assert store.read_repo_brief("/repos/other") == ""


# ---- settle writeback --------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_folds_worker_repo_notes_into_the_brief(tmp_path):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")  # workspace_dir=/repos/demo
    store.save_status(
        "g", GoalStatus(
            phase="in_flight",
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="added /health",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
        repo_notes="tests need `npm run test:ci`, not `npm test`; build is pnpm-only",
    ))

    await _tick(store, "g", engine)

    brief = store.read_repo_brief("/repos/demo")
    assert "tests need `npm run test:ci`" in brief


@pytest.mark.asyncio
async def test_settle_without_repo_notes_writes_nothing(tmp_path):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight",
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="added /health", gate_passed=True,
    ))

    await _tick(store, "g", engine)

    assert store.read_repo_brief("/repos/demo") == ""


@pytest.mark.asyncio
async def test_repo_notes_writeback_failure_never_wedges_the_settle(tmp_path, monkeypatch):
    """The brief is cross-goal hint material — a store hiccup on the writeback
    must not fail the settle or leave the ref in flight (loud-failure applies
    to the SETTLE; the notes are best-effort by contract)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight",
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="ok", gate_passed=True,
        repo_notes="a fact",
    ))
    monkeypatch.setattr(
        store, "write_repo_brief",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    await _tick(store, "g", engine)

    # settled despite the hiccup: the original ref cleared and the thin path
    # moved straight on to proposing done (the done-gate review is in flight).
    s = store.load_status("g")
    assert s.phase == "verifying"
    assert s.in_flight is not None and s.in_flight.is_done_check


# ---- dispatch injection ------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_prepends_the_repo_brief_to_the_goal_text(tmp_path):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")  # no STATUS yet → cadence due → advance dispatch
    store.write_repo_brief("/repos/demo", "build is pnpm-only")
    engine = FakeEngine()

    out = await _tick(store, "g", engine)

    assert out is Outcome.DISPATCHED
    dispatched_goal = engine.dispatched[0][0].goal
    assert dispatched_goal.startswith("[Repo notes")
    assert "build is pnpm-only" in dispatched_goal
    # the brief is a PREFIX — the advance brief itself rides byte-unchanged
    assert dispatched_goal.endswith(_advance_brief(store.load_effective_goal("g"), ""))


@pytest.mark.asyncio
async def test_review_dispatch_stays_unseeded_by_the_brief(tmp_path):
    """A read-only review grounds the evaluator — seeding it with prior
    workers' claims would bias the very reality-check the loop leans on. The
    thin path's review dispatch is the done-gate: trigger it via a successful
    advance settle and assert the reviewer's brief stays clean."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.write_repo_brief("/repos/demo", "build is pnpm-only")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight",
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="ok", gate_passed=True,
    ))

    out = await _tick(store, "g", engine)

    assert out is Outcome.VERIFYING
    reviews = [a for a, _g, _u in engine.dispatched if a.tool == "review_repository"]
    assert len(reviews) == 1
    assert "Repo notes" not in reviews[0].goal
    assert "pnpm-only" not in reviews[0].goal


@pytest.mark.asyncio
async def test_empty_brief_leaves_the_goal_text_byte_identical(tmp_path):
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    engine = FakeEngine()

    await _tick(store, "g", engine)

    # no brief stored → the dispatched goal is EXACTLY the advance brief
    assert engine.dispatched[0][0].goal == _advance_brief(store.load_effective_goal("g"), "")


@pytest.mark.asyncio
async def test_delivery_record_stays_clean_of_the_repo_brief(tmp_path):
    """The brief is worker INPUT, not evidence: the settled delivery record
    (→ the direction evaluator's "grounded deliveries" section) must carry the
    clean action text, never the prepended prior-run hints — otherwise every
    delivery re-presents unverified claims as shipped grounding
    (invariant-guard finding on this PR)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.write_repo_brief("/repos/demo", "build is pnpm-only")
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="ok", gate_passed=True,
    ))

    await _tick(store, "g", engine)   # dispatch (prefixed advance brief)
    await _tick(store, "g", engine)   # settle (then the done-gate opens)

    deliveries = store.recent_deliveries("g")
    assert "Advance this goal" in deliveries   # the clean advance-brief text
    assert "[Repo notes" not in deliveries
    assert "pnpm-only" not in deliveries


@pytest.mark.asyncio
async def test_idle_tick_stays_zero_token_with_a_brief_present(tmp_path):
    """The brief read happens ONLY on the dispatch path — an idle tick must
    not gain a read, an LLM call, or any other work (the quota guardrail)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    store.write_repo_brief("/repos/demo", "a fact")
    evaluator, engine = FakeClaude(), FakeEngine()

    out = await _tick(store, "g", engine, evaluator=evaluator)

    assert out is Outcome.IDLE
    assert evaluator.calls == 0
    assert engine.dispatched == []
