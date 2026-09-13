"""The tick rule (spec 046) — the zero-session invariants and the stops.

Driven with a fake world reader, a stub engine that never runs, and a
recording comment/merge seam. The number of task rows submitted IS the
quota assertion: an unchanged world spawns nothing, a blocked goal spawns
nothing, a red CI stops with the fact and is never retried."""

from __future__ import annotations

import json

import pytest

from devclaw.goal import github as gh
from devclaw.goal.donegate import parse_verdict
from devclaw.goal.tick import TickContext, tick_goal
from devclaw.goal.world import WorldReader
from devclaw.state_store import EXIT_DELIVERED, EXIT_DONE, EXIT_REVIEW, StateStore
from devclaw.task_queue import TaskQueue

REPO = "https://github.com/o/r"


class FakeWorld:
    def __init__(self, *, pr=None, issues=None, comments=None, creds=()):
        self.pr = pr or gh.PrFacts("none")
        self.issues = issues or [gh.Issue(1, "Add /health", "## Done when\n- /health returns 200", "open")]
        self.comments = comments or []
        self.creds = creds

    def reader(self) -> WorldReader:
        async def pr(repo_url, branch):
            return self.pr

        async def issue(repo_url, n):
            return next(i for i in self.issues if i.number == n)

        async def comments(repo_url, n):
            return [c for c in self.comments if c.number == n]

        return WorldReader(pr=pr, issue=issue, comments=comments, credentials=lambda: tuple(self.creds))


class Recorder:
    def __init__(self, merge_outcome=("merged", "")):
        self.comments: list[tuple[int, str]] = []
        self.merges: list[str] = []
        self.pings: list[str] = []
        self.merge_outcome = merge_outcome
        self._next_id = 1000

    async def post_comment(self, repo_url, number, body):
        self._next_id += 1
        self.comments.append((number, body))
        return f"https://github.com/o/r/issues/{number}#issuecomment-{self._next_id}"

    async def merge(self, repo_url, pr_url):
        self.merges.append(pr_url)
        return self.merge_outcome

    async def send(self, text):
        self.pings.append(text)
        return True


def _comment(id_, number, body, marker_kind="", **fields):
    if marker_kind:
        body = gh.marker(marker_kind, **fields) + "\n" + body
    kind, fs = gh.parse_marker(body)
    return gh.Comment(id=id_, number=number, author="denys", body=body, url="", created_at="2026-09-13",
                      marker=kind, marker_fields=fs)


@pytest.fixture
def harness(tmp_path):
    store = StateStore(str(tmp_path / "t.db"))

    async def never(req):  # the engine must not run in these tests
        raise AssertionError("engine ran")

    queue = TaskQueue(store, runner=never)
    rec = Recorder()
    ws = tmp_path / "proj"
    ws.mkdir()
    goal = store.create_goal(id="g1", project_id="p", workspace_dir=str(ws), repo_url=REPO,
                             objective="o", issues=[1], branch="goal/g1")

    def ctx(world: FakeWorld) -> TickContext:
        return TickContext(store=store, queue=queue, world=world.reader(), notifier=rec,
                           post_comment=rec.post_comment, merge=rec.merge)

    return store, queue, rec, goal, ctx


def _sessions(store, goal_id):
    return store.list_tasks(parent_goal_id=goal_id, limit=50)


async def test_a_fresh_goal_spawns_one_session_and_records_the_world(harness):
    store, queue, rec, goal, ctx = harness
    world = FakeWorld()
    assert await tick_goal(goal, ctx(world)) == "spawned"
    tasks = _sessions(store, goal.id)
    assert len(tasks) == 1 and tasks[0].kind == "implement_feature"
    assert store.get_goal(goal.id).last_seen_json == json.dumps(
        {"pr": "none", "head": "", "ci": "none", "instruction": 0, "record": 0,
         "credentials": [], "issues": [[1, "open"]]}, sort_keys=True)


async def test_an_unchanged_world_spawns_nothing(harness):
    """Pillar 6: reading the world is free; a session spawns only when it moved."""
    store, queue, rec, goal, ctx = harness
    world = FakeWorld()
    await tick_goal(goal, ctx(world))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", exit="NOTHING", exit_detail="nothing to do")
    for _ in range(3):
        assert await tick_goal(store.get_goal(goal.id), ctx(world)) == "idle"
    assert len(_sessions(store, goal.id)) == 1


async def test_a_running_session_holds_the_goal(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    assert await tick_goal(goal, ctx(FakeWorld(pr=gh.PrFacts("open", 5, "u", "abc", ci="green")))) == "running"
    assert len(_sessions(store, goal.id)) == 1


async def test_a_blocked_session_posts_its_question_once_and_waits(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", exit="BLOCKED", exit_detail="Postgres or SQLite? — default: SQLite")
    assert await tick_goal(goal, ctx(FakeWorld())) == "blocked"
    assert len(rec.comments) == 1 and "devclaw:block" in rec.comments[0][1]
    assert "SQLite" in rec.comments[0][1] and len(rec.pings) == 1
    # the block is now on the thread: still blocked, nothing posted twice, nothing spawned
    world = FakeWorld(comments=[_comment(10, 1, "q", "block", task=t.id, head="-", why="session_blocked")])
    assert await tick_goal(goal, ctx(world)) == "blocked"
    assert len(rec.comments) == 1 and len(_sessions(store, goal.id)) == 1


async def test_an_owner_instruction_newer_than_the_block_wakes_the_goal(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", exit="BLOCKED", exit_detail="which db?")
    world = FakeWorld(comments=[
        _comment(10, 1, "q", "block", task=t.id, head="-", why="session_blocked"),
        _comment(11, 1, "@devclaw use SQLite"),
    ])
    assert await tick_goal(goal, ctx(world)) == "spawned"
    assert len(_sessions(store, goal.id)) == 2


async def test_a_plain_comment_is_not_an_instruction(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", exit="BLOCKED", exit_detail="which db?")
    world = FakeWorld(comments=[
        _comment(10, 1, "q", "block", task=t.id, head="-", why="session_blocked"),
        _comment(11, 1, "I think SQLite, will decide tomorrow"),
    ])
    assert await tick_goal(goal, ctx(world)) == "blocked"
    assert len(_sessions(store, goal.id)) == 1


async def test_a_red_ci_on_a_delivered_head_stops_and_is_never_retried(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DELIVERED, exit_detail="x")
    red = gh.PrFacts("open", 5, "u", "abc123", ci="red", ci_detail="1 failing: tests",
                     failing_names=("tests",), failing_logs=(("tests", "FAILED test_x"),))
    assert await tick_goal(goal, ctx(FakeWorld(pr=red))) == "blocked"
    assert "FAILED test_x" in rec.comments[0][1] and "head=abc123" in rec.comments[0][1]
    world = FakeWorld(pr=red, comments=[_comment(20, 5, "r", "block", task=t.id, head="abc123", why="red_ci")])
    for _ in range(3):
        assert await tick_goal(goal, ctx(world)) == "blocked"
    assert len(_sessions(store, goal.id)) == 1 and len(rec.comments) == 1


async def test_done_with_green_ci_spawns_the_review_session_once(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE, exit_detail="all clauses met")
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    assert await tick_goal(goal, ctx(FakeWorld(pr=gh.PrFacts("open", 5, "u", "abc123", ci="pending")))) == "ci pending"
    assert await tick_goal(goal, ctx(FakeWorld(pr=green))) == "gate"
    tasks = _sessions(store, goal.id)
    assert len(tasks) == 2 and tasks[0].kind == "review_repository" and not tasks[0].deliver


async def test_an_achieved_verdict_merges_and_closes(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    await tick_goal(goal, ctx(FakeWorld(pr=green)))
    review = _sessions(store, goal.id)[0]
    store.claim_pending(review.id)
    verdict = '```json\n{"achieved": true, "clauses": [{"clause": "/health returns 200", "satisfied": true, "evidence": "app.py:health, tests/test_health.py"}], "summary": "ok"}\n```'
    store.mark_done(review.id, json.dumps({"agent_output": verdict}), exit=EXIT_REVIEW)
    assert await tick_goal(goal, ctx(FakeWorld(pr=green))) == "closed"
    assert rec.merges == ["u"] and "achieved=1" in rec.comments[0][1]
    assert store.get_goal(goal.id).outcome == "achieved"


async def test_a_refused_verdict_stops_the_goal_with_no_correction_round(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    await tick_goal(goal, ctx(FakeWorld(pr=green)))
    review = _sessions(store, goal.id)[0]
    store.claim_pending(review.id)
    verdict = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": false, "evidence": "missing"}]}\n```'
    store.mark_done(review.id, json.dumps({"agent_output": verdict}), exit=EXIT_REVIEW)
    assert await tick_goal(goal, ctx(FakeWorld(pr=green))) == "blocked"
    assert rec.merges == [] and "achieved=0" in rec.comments[0][1]
    world = FakeWorld(pr=green, comments=[_comment(30, 5, "v", "verdict", head="abc123", task=review.id, achieved="0", unreadable="0")])
    assert await tick_goal(goal, ctx(world)) == "blocked"
    assert len(_sessions(store, goal.id)) == 2


def test_the_verdict_claim_never_beats_the_clauses():
    """Pillar 7, mechanically: 'achieved' with an unsatisfied clause is not achieved,
    a question is not an achievement, no JSON is unreadable (fails closed)."""
    assert not parse_verdict('```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": ""}]}\n```').achieved
    assert not parse_verdict('```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "x"}], "question": "which?"}\n```').achieved
    assert parse_verdict("I think it is fine.").unreadable
    assert parse_verdict('```json\n{"achieved": true, "clauses": []}\n```').unreadable
    assert parse_verdict('```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "x"}]}\n```').achieved


async def test_a_merged_pr_closes_the_goal(harness):
    store, queue, rec, goal, ctx = harness
    assert await tick_goal(goal, ctx(FakeWorld(pr=gh.PrFacts("merged", 5, "u", "abc")))) == "closed"
    assert store.get_goal(goal.id).outcome == "achieved" and len(_sessions(store, goal.id)) == 0


async def test_the_daily_session_cap_is_read_from_the_rows(harness, monkeypatch):
    from devclaw import config as _config

    monkeypatch.setattr(_config, "sessions_per_day", lambda: 1)
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_failed(t.id, "timeout", exit="INTERRUPTED", exit_detail="timeout")
    assert await tick_goal(goal, ctx(FakeWorld())) == "capped"
    assert len(_sessions(store, goal.id)) == 1


async def test_an_interrupted_session_resumes_without_a_world_change(harness):
    store, queue, rec, goal, ctx = harness
    world = FakeWorld()
    await tick_goal(goal, ctx(world))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_failed(t.id, "timeout", exit="INTERRUPTED", exit_detail="timeout")
    assert await tick_goal(store.get_goal(goal.id), ctx(world)) == "resumed"
    assert len(_sessions(store, goal.id)) == 2


async def test_a_green_credential_wakes_a_goal_blocked_on_it(harness):
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", exit="NOTHING")
    assert await tick_goal(store.get_goal(goal.id), ctx(FakeWorld())) == "idle"
    assert await tick_goal(store.get_goal(goal.id), ctx(FakeWorld(creds=("NODE_AUTH_TOKEN",)))) == "spawned"


async def test_one_session_per_project_lane(tmp_path):
    from devclaw.goal.service import GoalService

    store = StateStore(str(tmp_path / "t.db"))

    async def never(req):
        raise AssertionError("engine ran")

    queue = TaskQueue(store, runner=never)
    rec = Recorder()
    ws = tmp_path / "proj"
    ws.mkdir()
    for gid in ("a", "b"):
        store.create_goal(id=gid, project_id="p", workspace_dir=str(ws), repo_url=REPO,
                          objective="o", issues=[1 if gid == "a" else 2], branch=f"goal/{gid}")
    world = FakeWorld(issues=[gh.Issue(1, "one", "x", "open"), gh.Issue(2, "two", "y", "open")])
    svc = GoalService(queue, store, notifier=rec, world_reader=world.reader(),
                      post_comment=rec.post_comment, merge=rec.merge, tick_seconds=900)
    outcomes = await svc.tick_all()
    assert outcomes == {"a": "spawned", "b": "lane busy"}
    assert len(store.list_tasks(limit=10)) == 1
