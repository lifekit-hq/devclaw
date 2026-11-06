"""The tick rule (spec 046) — the zero-session invariants and the stops.

Driven with a fake world reader, a stub engine that never runs, and a
recording comment/merge seam. The number of task rows submitted IS the
quota assertion: an unchanged world spawns nothing, a blocked goal spawns
nothing, a red CI stops with the fact and is never retried."""

from __future__ import annotations

import json

import pytest

from devclaw.goal import github as gh
from devclaw.goal.donegate import parse_verdict, scope_extras
from devclaw.goal.tick import TickContext, _scope_gate, tick_goal
from devclaw.goal.world import WorldReader
from devclaw.state_store import EXIT_DELIVERED, EXIT_DONE, EXIT_REVIEW, StateStore
from devclaw.task_queue import TaskQueue

REPO = "https://github.com/o/r"


def _lease_in_place(project_ws, repo_url, goal_id):
    """No treehouse in the stubbed suite: the goal's worktree is the project dir."""
    async def leased():
        return project_ws
    return leased()


BASE_TREE = frozenset({"devclaw/goal/donegate.py", "devclaw/goal/world.py", "devclaw/engine/claude.py", "docs/flows",
                       "docs/flows/delivery.md", "docs/INDEX.md", "pyproject.toml", "uv.lock"})


class FakeWorld:
    def __init__(self, *, pr=None, issues=None, comments=None, creds=(), files=(), tree=BASE_TREE, login="host"):
        self.pr = pr or gh.PrFacts("none")
        self.issues = issues or [gh.Issue(1, "Add /health", "## Done when\n- /health returns 200", "open")]
        self.comments = comments or []
        self.creds = creds
        self.files = files  # what the PR changes; None = unreadable
        self.tree = tree  # the base branch's paths; None = unreadable
        self.login = login  # the account the host's gh acts as; "" = unreadable

    def reader(self) -> WorldReader:
        async def pr(repo_url, branch):
            return self.pr

        async def issue(repo_url, n):
            return next(i for i in self.issues if i.number == n)

        async def comments(repo_url, n):
            return [c for c in self.comments if c.number == n]

        async def files(repo_url, number):
            return self.files

        async def tree(repo_url, base):
            return self.tree

        async def login():
            return self.login

        return WorldReader(pr=pr, issue=issue, comments=comments, credentials=lambda: tuple(self.creds),
                           files=files, tree=tree, login=login)


class Recorder:
    def __init__(self, merge_outcome=("merged", "")):
        self.comments: list[tuple[int, str]] = []
        self.merges: list[tuple[str, str]] = []
        self.pings: list[str] = []
        self.merge_outcome = merge_outcome
        self._next_id = 1000

    async def post_comment(self, repo_url, number, body):
        self._next_id += 1
        self.comments.append((number, body))
        return f"https://github.com/o/r/issues/{number}#issuecomment-{self._next_id}"

    async def merge(self, repo_url, pr_url, head):
        self.merges.append((pr_url, head))
        return self.merge_outcome

    async def send(self, text):
        self.pings.append(text)
        return True


def _comment(id_, number, body, marker_kind="", author="denys", association="", **fields):
    if marker_kind:
        body = gh.marker(marker_kind, **fields) + "\n" + body
    kind, fs = gh.parse_marker(body)
    return gh.Comment(id=id_, number=number, author=author, body=body, url="", created_at="2026-09-13",
                      marker=kind, marker_fields=fs, association=association)


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
                           post_comment=rec.post_comment, merge=rec.merge, lease=_lease_in_place)

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


SCOPED = gh.Issue(1, "Fix the gate", "Touch `devclaw/goal/donegate.py` and `docs/flows/`.\n## Done when\n- it holds", "open")


async def test_the_scope_gate_fails_closed_before_any_review_runs(harness):
    """Fail-closed gate: a PR changing a file the issue and the owner name
    nowhere never reaches a review session. An unreadable file list never
    passes; the first excess gets ONE correction session, the second stops the
    goal; the owner's newer word waives. A named file, its test, a named
    directory and the .devclaw manifest are in scope; text naming no file
    states no scope."""
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld(issues=[SCOPED])))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    inside = ("devclaw/goal/donegate.py", "tests/test_donegate.py", "docs/flows/delivery.md", "docs/INDEX.md", ".devclaw/verify")
    over = inside + ("devclaw/goal/world.py",)

    assert await tick_goal(goal, ctx(FakeWorld(issues=[SCOPED], pr=green, files=None))) == "unreadable"
    assert await tick_goal(goal, ctx(FakeWorld(issues=[SCOPED], pr=green, files=over, tree=None))) == "unreadable"
    assert len(_sessions(store, goal.id)) == 1

    assert await tick_goal(goal, ctx(FakeWorld(issues=[SCOPED], pr=green, files=over))) == "resumed"
    correction = _sessions(store, goal.id)[0]
    assert correction.kind == "implement_feature" and "SCOPE_EXCEEDED" in rec.comments[-1][1]
    assert "devclaw/goal/world.py" in rec.comments[-1][1] and "donegate.py" not in rec.comments[-1][1].split("nowhere:")[1]
    store.claim_pending(correction.id)
    store.mark_done(correction.id, "{}", pr_url="u", exit=EXIT_DONE)

    record = _comment(40, 5, "x", "scope", head="abc123", task=t.id)
    assert await tick_goal(goal, ctx(FakeWorld(issues=[SCOPED], pr=green, files=over, comments=[record]))) == "blocked"
    assert "SCOPE_EXCEEDED" in rec.comments[-1][1] and len(_sessions(store, goal.id)) == 2

    # the waiver is per head: a later owner comment never disables the check for a new head
    h3 = gh.PrFacts("open", 5, "u", "def456", ci="green")
    later = FakeWorld(issues=[SCOPED], pr=h3, files=over, comments=[record, _comment(41, 5, "@devclaw also fix Y")])
    assert await tick_goal(goal, ctx(later)) == "blocked" and "SCOPE_EXCEEDED" in rec.comments[-1][1]
    # ...while an owner reply to the SCOPE_EXCEEDED block waives the head that block was posted on
    block = _comment(50, 5, "x", "block", task="t", head="def456", why="SCOPE_EXCEEDED")
    replied = FakeWorld(issues=[SCOPED], pr=h3, files=over, comments=[record, block, _comment(51, 5, "@devclaw ok")])
    assert await _scope_gate(ctx(replied), goal, await ctx(replied).world.read(goal), correction) is None

    answered = FakeWorld(issues=[SCOPED], pr=green, files=over,
                         comments=[record, _comment(41, 5, "@devclaw those are fine")])
    assert await tick_goal(goal, ctx(answered)) == "gate"  # the owner's word waives the check

    assert scope_extras(over, "no file is named here", BASE_TREE) == []
    # prose that merely looks like a file, or a directory that is not in the tree, names no scope
    claude = ("devclaw/engine/claude.py",)
    for prose in ("Bug in Node.js handling", "Upgrade Next.js and Vue.js", "use a layout like foo/bar/ here",
                  "see https://docs.python.org/3/library/json.html and https://github.com/o/r/blob/main/README.md"):
        assert scope_extras(claude, prose, BASE_TREE) == []
    # a real file: by '/' (even a new one), or by existing in the base tree; a tree directory counts
    assert scope_extras(claude, "Fix new/thing.py", BASE_TREE) == list(claude)
    assert scope_extras(claude, "Fix world.py", BASE_TREE) == list(claude)
    # any extension names a file, not a closed list: .go, .csproj, .razor
    for name in ("cmd/server/main.go", "src/Api/Api.csproj", "src/Pages/Index.razor"):
        assert scope_extras((name, "devclaw/x.py"), f"Fix the handler in {name}; see docs.", BASE_TREE) == ["devclaw/x.py"]
    assert scope_extras(("main.go",), "Fix main.go.", BASE_TREE | {"main.go"}) == []
    assert scope_extras(("docs/flows/x.md",), "see docs/flows/ and tick.py", BASE_TREE | {"tick.py"}) == []
    # docs, the spec/test of a named file and a lockfile of a named or changed manifest ride along
    named = "Edit devclaw/goal/tick.py and frontend/src/a/foo.ts"
    companions = ("docs/INDEX.md", "frontend/src/a/foo.spec.ts", "tests/test_tick.py", "frontend/package-lock.json")
    assert scope_extras(("devclaw/goal/tick.py",) + companions, named, BASE_TREE) == ["frontend/package-lock.json"]
    assert scope_extras(("frontend/package.json",) + companions[-1:], named + " and frontend/package.json", BASE_TREE) == []
    assert scope_extras(("frontend/package.json", "frontend/package-lock.json", "devclaw/x.py"), named, BASE_TREE) == [
        "frontend/package.json", "devclaw/x.py"]


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
    assert rec.merges == [("u", "abc123")] and "achieved=1" in rec.comments[0][1]
    assert store.get_goal(goal.id).outcome == "achieved"


@pytest.mark.parametrize("body, files", [
    ("## Done when\n- the users table migration runs on deploy", ()),
    ("## Done when\n- /health returns 200", ("db/migrations/0003_add_col.sql",)),
])
async def test_a_hard_to_reverse_change_never_merges_without_the_owners_merge(harness, body, files):
    """Issue 979, fail-closed: an achieved verdict on a migration/deletion/data
    change posts one decision and waits; only the owner's `merge` after it
    squash-merges. Nothing else spawns a session or merges."""
    store, queue, rec, goal, ctx = harness
    issues = [gh.Issue(1, "t", body, "open")]
    await tick_goal(goal, ctx(FakeWorld(issues=issues)))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    await tick_goal(goal, ctx(FakeWorld(pr=green, issues=issues, files=files)))
    review = _sessions(store, goal.id)[0]
    store.claim_pending(review.id)
    verdict = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "x"}]}\n```'
    store.mark_done(review.id, json.dumps({"agent_output": verdict}), exit=EXIT_REVIEW)
    assert await tick_goal(goal, ctx(FakeWorld(pr=green, issues=issues, files=files))) == "blocked"
    assert rec.merges == [] and "why=hard_to_reverse" in rec.comments[-1][1] and "merge" in rec.pings[-1]
    records = [_comment(30, 5, "v", "verdict", head="abc123", task=review.id, achieved="1", unreadable="0"),
               _comment(31, 5, "b", "block", task="-", head="abc123", why="hard_to_reverse")]

    def world(*owner, login="host"):
        return FakeWorld(pr=green, issues=issues, files=files, comments=[*records, *owner], login=login)
    assert await tick_goal(goal, ctx(world())) == "blocked"
    assert len(rec.comments) == 2
    ask = _comment(33, 5, "b", "block", task="-", head="abc123", why="hard_to_reverse")
    for i, answer in enumerate(("@devclaw looks fine", "@devclaw do not merge", "@devclaw merge it later")):
        before = len(rec.comments)
        assert await tick_goal(goal, ctx(world(_comment(32, 5, answer)))) == "blocked"
        assert len(rec.comments) == before + 1 and "waits for a merge decision" in rec.comments[-1][1]
        assert await tick_goal(goal, ctx(world(_comment(32, 5, answer), ask))) == "blocked"  # answered once, never again
        assert len(rec.comments) == before + 1
    assert rec.merges == [] and len(_sessions(store, goal.id)) == 2
    assert await tick_goal(goal, ctx(world(_comment(29, 5, "@devclaw merge", author="o", association="OWNER")))) == "blocked"  # older than the ask
    for stranger in (dict(author="mallory", association="NONE"), dict(author="", association=""),
                     dict(author="mallory", association="MEMBER"), dict(author="mallory", association="COLLABORATOR")):
        before = len(rec.comments)
        assert await tick_goal(goal, ctx(world(_comment(34, 5, "@devclaw merge", **stranger)))) == "blocked"
        assert len(rec.comments) == before + 1
    assert await tick_goal(goal, ctx(world(_comment(34, 5, "@devclaw merge", author="host"), login=""))) == "blocked"
    assert "OWNER" in rec.comments[-1][1] and rec.merges == []
    for releaser, login in ((dict(author="host", association="MEMBER"), "host"), (dict(author="HOST", association="MEMBER"), "host"),
                            (dict(author="denys", association="OWNER"), "host"), (dict(author="denys", association="OWNER"), "")):
        assert await tick_goal(goal, ctx(world(_comment(34, 5, "@devclaw decide: merge", **releaser), login=login))) == "closed"
        assert rec.merges == [("u", "abc123")]
        rec.merges.clear()


@pytest.mark.parametrize("settle, outcome, unreadable", [
    ("refused", "blocked", "0"),
    ("unreadable", "gate unreadable", "1"),
    ("read-only broken", "gate unreadable", "1"),
])
async def test_a_refused_verdict_stops_the_goal_with_no_correction_round(harness, settle, outcome, unreadable):
    """A verdict that is not achieved never merges and spawns nothing; only the
    owner's answer on the thread wakes the goal."""
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    green = gh.PrFacts("open", 5, "u", "abc123", ci="green")
    await tick_goal(goal, ctx(FakeWorld(pr=green)))
    review = _sessions(store, goal.id)[0]
    store.claim_pending(review.id)
    if settle == "refused":
        verdict = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": false, "evidence": "missing"}]}\n```'
        store.mark_done(review.id, json.dumps({"agent_output": verdict}), exit=EXIT_REVIEW)
    elif settle == "unreadable":
        store.mark_done(review.id, json.dumps({"agent_output": "Looks good to me."}), exit=EXIT_REVIEW)
    else:
        achieved = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "x"}]}\n```'
        store.mark_failed(review.id, "the review session moved HEAD", exit=EXIT_REVIEW,
                          result_json=json.dumps({"agent_output": achieved}))
    assert await tick_goal(goal, ctx(FakeWorld(pr=green))) == outcome
    assert rec.merges == [] and "achieved=0" in rec.comments[0][1] and f"unreadable={unreadable}" in rec.comments[0][1]
    assert len(rec.pings) == 1 and "done-gate refused" in rec.pings[0]
    verdict_record = _comment(30, 5, "v", "verdict", head="abc123", task=review.id, achieved="0", unreadable=unreadable)
    world = FakeWorld(pr=green, comments=[verdict_record])
    assert await tick_goal(goal, ctx(world)) == "blocked"
    assert len(_sessions(store, goal.id)) == 2 and rec.merges == []
    answered = FakeWorld(pr=green, comments=[verdict_record, _comment(31, 5, "@devclaw keep going")])
    assert await tick_goal(store.get_goal(goal.id), ctx(answered)) == "spawned"
    assert _sessions(store, goal.id)[0].kind == "implement_feature" and rec.merges == []


async def test_a_verdict_counts_only_for_the_head_it_read(harness):
    """The PR moved while the review ran: its achieved verdict is about a head
    that is no longer delivered, so nothing merges and a fresh session starts."""
    store, queue, rec, goal, ctx = harness
    await tick_goal(goal, ctx(FakeWorld()))
    t = _sessions(store, goal.id)[0]
    store.claim_pending(t.id)
    store.mark_done(t.id, "{}", pr_url="u", exit=EXIT_DONE)
    await tick_goal(goal, ctx(FakeWorld(pr=gh.PrFacts("open", 5, "u", "abc123", ci="green"))))
    review = _sessions(store, goal.id)[0]
    store.claim_pending(review.id)
    store.set_task_pre_run_sha(review.id, "abc123")
    verdict = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "x"}]}\n```'
    store.mark_done(review.id, json.dumps({"agent_output": verdict}), exit=EXIT_REVIEW)
    moved = gh.PrFacts("open", 5, "u", "def456", ci="green")
    assert await tick_goal(store.get_goal(goal.id), ctx(FakeWorld(pr=moved))) == "spawned"
    assert rec.merges == [] and rec.comments == []


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


@pytest.mark.parametrize("kind, outcome", [("implement_feature", "resumed"), ("review_repository", "gate")])
async def test_an_interrupted_session_resumes_without_a_world_change(harness, kind, outcome):
    """A fresh session of the same kind: an interrupted review is a review again,
    never a code session that could propose DONE past the gate."""
    store, queue, rec, goal, ctx = harness
    world = FakeWorld()
    tid = queue.submit(kind=kind, workspace_dir=goal.workspace_dir, goal="g", parent_goal_id=goal.id, pump=False)
    store.claim_pending(tid)
    store.mark_failed(tid, "timeout", exit="INTERRUPTED", exit_detail="timeout")
    assert await tick_goal(store.get_goal(goal.id), ctx(world)) == outcome
    tasks = _sessions(store, goal.id)
    assert len(tasks) == 2 and tasks[0].kind == kind


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
                      post_comment=rec.post_comment, merge=rec.merge, lease=_lease_in_place, tick_seconds=900)
    outcomes = await svc.tick_all()
    assert outcomes == {"a": "spawned", "b": "lane busy"}
    assert len(store.list_tasks(limit=10)) == 1
