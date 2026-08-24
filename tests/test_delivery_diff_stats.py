"""Diff stats ride the settle chain: gate-time diff → result → DeliveryEvent.

The per-goal run summary needs files/insertions/deletions per delivery, and
nothing captured them — the only diff-stat code was the trend detector's
shortstat parse at harness-self scope. The queue now counts them from the
SAME diff text the gates judged (no extra git call) and stamps them onto the
task result; the goal engine surfaces them on PollResult; the settle path
records them on the DeliveryEvent. Best-effort at every hop — a stats hiccup
never fails a task or blocks a settle.
"""

import json

import pytest

from devclaw.queue import settle as queue_settle
from devclaw.engine import EngineRequest
from devclaw.goal.engine import _diff_stats as poll_diff_stats
from devclaw.loom import trace
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue, _diff_stats

_DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
-old line
+new line
+another new line
 context
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
+docs line
"""


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


# ---- the pure counter -------------------------------------------------------


def test_diff_stats_counted_from_diff_text():
    assert _diff_stats(_DIFF) == {"files": 2, "insertions": 3, "deletions": 1}


def test_diff_stats_none_on_empty_diff():
    assert _diff_stats("") is None
    assert _diff_stats("   \n") is None


def test_diff_stats_ignores_header_lines():
    # +++/--- headers must not count as insertions/deletions
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert _diff_stats(diff) == {"files": 1, "insertions": 1, "deletions": 1}


# ---- queue stamps stats onto the done result --------------------------------


async def test_done_result_carries_diff_stats(store, monkeypatch):
    async def fake_diff(host_dir, base="", head=""):
        return _DIFF

    monkeypatch.setattr(queue_settle, "_git_diff", fake_diff)

    async def runner(req: EngineRequest):
        gate = {"ran": True, "cmd": "pytest", "passed": True,
                "exit_code": 0, "timed_out": False, "output": ""}
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done"
    result = json.loads(t.result_json)
    assert result["diff_stats"] == {"files": 2, "insertions": 3, "deletions": 1}


async def test_done_result_omits_diff_stats_on_empty_diff(store, monkeypatch):
    # degrade-to-absent: an empty diff (or a git hiccup upstream returning "")
    # yields NO diff_stats key — never a crash, never a fake zero row
    async def fake_diff(host_dir, base="", head=""):
        return ""

    monkeypatch.setattr(queue_settle, "_git_diff", fake_diff)

    async def runner(req: EngineRequest):
        gate = {"ran": True, "cmd": "pytest", "passed": True,
                "exit_code": 0, "timed_out": False, "output": ""}
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done"
    assert "diff_stats" not in json.loads(t.result_json)


# ---- poll extraction is defensive -------------------------------------------


def test_poll_diff_stats_extraction_defensive():
    good = json.dumps({"diff_stats": {"files": 1, "insertions": 2, "deletions": 0}})
    assert poll_diff_stats(good) == {"files": 1, "insertions": 2, "deletions": 0}
    # malformed shapes → None, never a crash
    assert poll_diff_stats(json.dumps({"diff_stats": "nope"})) is None
    assert poll_diff_stats(json.dumps({"diff_stats": {"files": "x"}})) is None
    assert poll_diff_stats(json.dumps({})) is None
    assert poll_diff_stats(None) is None


# ---- DeliveryEvent persists the fields --------------------------------------


def test_delivery_event_records_diff_stats():
    captured: list = []

    class _Sink:
        def append(self, ev):
            captured.append(ev)

    token = trace._current.set(_Sink())
    try:
        trace.record_delivery(
            goal_id="g1", action_label="task x", gate_passed=True,
            pr_url="https://pr", diff_stats={"files": 2, "insertions": 3, "deletions": 1},
        )
        trace.record_delivery(
            goal_id="g1", action_label="task y", gate_passed=True, pr_url="",
        )
    finally:
        trace._current.reset(token)
    with_stats, without = captured
    assert (with_stats.diff_files, with_stats.diff_insertions, with_stats.diff_deletions) == (2, 3, 1)
    # absent stats stay None — no fake zeros in the record
    assert (without.diff_files, without.diff_insertions, without.diff_deletions) == (None, None, None)


# ---- the projection reports the span that ships (spec 013 FR-009) -----------


def test_a_binary_file_is_counted_and_named_instead_of_under_reported():
    """FR-009: a binary file contributes no +/- lines to a unified diff, so a
    span carrying one is a BOUNDED view of its own size. A bounded view says so
    — silently reporting "+0" for a 4MB asset is the under-reporting the spec
    forbids."""
    diff = (
        "diff --git a/logo.png b/logo.png\n"
        "new file mode 100644\n"
        "Binary files /dev/null and b/logo.png differ\n"
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert _diff_stats(diff) == {
        "files": 2, "insertions": 1, "deletions": 1, "binary": 1,
    }


def test_a_text_only_span_carries_no_binary_key():
    assert "binary" not in _diff_stats(_DIFF)


async def test_the_projection_counts_files_the_agent_never_recorded(store, tmp_path):
    """SC-002 through the projection: the reported size is the size of what
    ships, because both read the same materialized object. The 2026-08-22 run
    reported 1 file / +32 while shipping 4 files / +179."""
    import subprocess

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)
    (ws / "README.md").write_text("# base\n")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "base"],
                   check=True, capture_output=True)

    async def runner(req: EngineRequest):
        (ws / "README.md").write_text("# base\nedited\n")
        for name, n in (("AGENTS.md", 41), ("ARCHITECTURE.md", 94)):
            (ws / name).write_text("x\n" * n)
        gate = {"ran": True, "cmd": "pytest", "passed": True,
                "exit_code": 0, "timed_out": False, "output": ""}
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest")
    await q.drain()

    stats = json.loads(store.get_task(tid).result_json)["diff_stats"]
    assert stats["files"] == 3
    assert stats["insertions"] == 41 + 94 + 1
