"""Spec 015 US2 — the host half: a validate_product task settles through its
own spine (no gates, no delivery, no retry), files spec-014 findings, and
leaves a run record."""

from __future__ import annotations

import json

import pytest

from devclaw import issue_doorway as dw
from devclaw import validation_loop as vl
from devclaw.engine import EngineRequest
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


class FakeGh:
    instances: list = []

    def __init__(self):
        self.created: list[dict] = []
        self.comments: list = []
        self._next = 200
        FakeGh.instances.append(self)

    async def ensure_label(self, repo, name):
        pass

    async def create_issue(self, repo, *, title, body, labels):
        self._next += 1
        self.created.append({"repo": repo, "title": title, "body": body,
                             "labels": labels, "number": self._next})
        return self._next

    async def comment_issue(self, repo, number, *, body):
        self.comments.append((repo, number, body))
        return True

    async def reopen_issue(self, repo, number, *, comment):
        return True


@pytest.fixture()
def fake_gh(monkeypatch):
    FakeGh.instances = []
    monkeypatch.setattr(dw, "GhCli", FakeGh)
    monkeypatch.setattr(vl, "repo_slug_for_workspace", lambda ws: "o/product")
    return FakeGh


def _workspace(tmp_path, *, contract=True):
    ws = tmp_path / "ws"
    ws.mkdir()
    if contract:
        (ws / "devclaw.json").write_text(json.dumps({
            "schemaVersion": 1,
            "validation": {"boot": "./boot.sh", "suites": "./suites.sh"},
        }))
    return str(ws)


def _runner_with_report(report, calls=None):
    async def runner(req: EngineRequest):
        if calls is not None:
            calls.append(req)
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "message": "validation", "validation_report": report}
    return runner


def _failing_report(titles):
    return {
        "contract_ran": True,
        "boot": {"passed": True, "exit_code": 0, "timed_out": False, "output_tail": ""},
        "suites": {"passed": False, "exit_code": 1, "timed_out": False,
                   "output_tail": "2 failed"},
        "browser_report": {"expected": 3, "unexpected": len(titles), "flaky": 0, "skipped": 0},
        "failing_tests": list(titles),
        "partial": False, "note": "",
    }


def _green_report():
    return {
        "contract_ran": True,
        "boot": {"passed": True, "exit_code": 0, "timed_out": False, "output_tail": ""},
        "suites": {"passed": True, "exit_code": 0, "timed_out": False, "output_tail": ""},
        "browser_report": {"expected": 5, "unexpected": 0, "flaky": 0, "skipped": 0},
        "failing_tests": [], "partial": False, "note": "",
    }


async def test_failing_scenario_files_one_schema_finding_no_pr_no_commit(
    store, tmp_path, fake_gh
):
    calls: list = []
    q = TaskQueue(store, runner=_runner_with_report(
        _failing_report(["checkout > coupon applies"]), calls))
    tid = q.submit(kind="validate_product", workspace_dir=_workspace(tmp_path),
                   goal="validate the product")
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "done"  # red suites are OUTPUT, not task failure
    assert row.pr_url is None
    assert "1 finding(s) filed" in (row.result_json or "")

    (gh,) = FakeGh.instances
    (created,) = gh.created
    parsed, version = dw.parse_machine_issue(created["body"], created["title"])
    assert version == dw.SCHEMA_VERSION
    assert parsed.source == "validator"
    assert parsed.fingerprint == "validator|checkout > coupon applies"
    assert parsed.spec_ref == "checkout > coupon applies"
    # the engine ran with the resolved contract, agent-less
    (req,) = calls
    assert req.kind == "validate_product"
    assert req.validation == {"boot": "./boot.sh", "suites": "./suites.sh"}
    assert req.verify_cmd is None


async def test_repeated_failure_deduplicates_by_fingerprint(store, tmp_path, fake_gh):
    report = _failing_report(["jobs > sentinel registers"])
    q = TaskQueue(store, runner=_runner_with_report(report))
    ws = _workspace(tmp_path)
    q.submit(kind="validate_product", workspace_dir=ws, goal="run 1")
    await q.drain()
    q.submit(kind="validate_product", workspace_dir=ws, goal="run 2")
    await q.drain()
    created = [c for gh in FakeGh.instances for c in gh.created]
    assert len(created) == 1  # SC-002: one issue across repeated runs
    row = store.machine_issue_get("o/product", "validator|jobs > sentinel registers")
    assert row["occurrence_count"] == 2


async def test_green_run_files_nothing_and_leaves_run_record(store, tmp_path, fake_gh):
    q = TaskQueue(store, runner=_runner_with_report(_green_report()))
    tid = q.submit(kind="validate_product", workspace_dir=_workspace(tmp_path),
                   goal="validate")
    await q.drain()
    row = store.get_task(tid)
    assert row.status == "done"
    assert "validation: green (5 executed)" in (row.result_json or "")
    assert all(not gh.created for gh in FakeGh.instances)


async def test_boot_failure_settles_failed_and_files_critical_finding(
    store, tmp_path, fake_gh
):
    report = {
        "contract_ran": True,
        "boot": {"passed": False, "exit_code": 7, "timed_out": False,
                 "output_tail": "db seed crashed"},
        "suites": None, "browser_report": None, "failing_tests": [],
        "partial": False, "note": "boot failed — the product did not come up",
    }
    q = TaskQueue(store, runner=_runner_with_report(report))
    tid = q.submit(kind="validate_product", workspace_dir=_workspace(tmp_path),
                   goal="validate")
    await q.drain()
    row = store.get_task(tid)
    assert row.status == "failed"
    assert "could not prove the running product" in (row.error or "")
    (gh,) = FakeGh.instances
    (created,) = gh.created
    parsed, _ = dw.parse_machine_issue(created["body"], created["title"])
    assert parsed.fingerprint == "validator|boot"
    assert parsed.severity == "critical"


async def test_missing_contract_fails_loud_and_files_finding(store, tmp_path, fake_gh):
    q = TaskQueue(store, runner=_runner_with_report(_green_report()))
    tid = q.submit(kind="validate_product",
                   workspace_dir=_workspace(tmp_path, contract=False),
                   goal="validate")
    await q.drain()
    row = store.get_task(tid)
    assert row.status == "failed"
    assert "no usable contract" in (row.error or "")
    (gh,) = FakeGh.instances
    (created,) = gh.created
    parsed, _ = dw.parse_machine_issue(created["body"], created["title"])
    assert parsed.fingerprint == "validator|missing-contract"


async def test_workspace_is_restored_after_the_run(store, tmp_path, fake_gh):
    """FR-005: boot/seed artifacts never become commits — the workspace is
    reset+cleaned after the run (a real git repo proves the mechanics)."""
    import subprocess
    ws = tmp_path / "ws"
    ws.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(ws), *args], check=True,
                       capture_output=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "PATH": "/usr/bin:/bin"})
    git("init", "-q")
    (ws / "devclaw.json").write_text(json.dumps({
        "schemaVersion": 1,
        "validation": {"boot": "./boot.sh", "suites": "./suites.sh"},
    }))
    git("add", "-A")
    git("commit", "-qm", "seed")

    async def runner(req: EngineRequest):
        (ws / "boot-artifact.db").write_text("seeded")
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "validation_report": _green_report()}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="validate_product", workspace_dir=str(ws), goal="validate")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert not (ws / "boot-artifact.db").exists()
