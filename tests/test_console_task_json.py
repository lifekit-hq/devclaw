"""``GET /tasks/{task_id}.json`` — the universal task drill-in feed.

Denys's ask (2026-08-10): every task row anywhere in the console — a goal's
dispatched tasks OR a project's standalone dispatch_task rows — must open into
the SAME detail anatomy: the contract (full prompt), the settled verdicts
(verify / delivery / diff stats / usage), and the execution trace. The trace
already had its feed (``/tasks/{id}/events.json``, #455); what was missing was
a task-row feed that doesn't require reaching through a goal — standalone tasks
were dead rows. These pins guard that feed's contract:

  * the full goal/prompt text rides along untruncated (the CONTRACT),
  * result_json is decomposed into verify/delivery/diffStats/usage WITHOUT
    the bulky agent_output (the events feed owns the transcript),
  * a corrupt result_json degrades to null verdicts, never a 500,
  * unknown id → 404 (a typo is not an empty task), bad id shape → 400.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

import devclaw.server.http as http_mod
from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = StateStore(str(tmp_path / "devclaw.db"))
    monkeypatch.setattr(http_mod, "store", s)
    return s


def _get(fn, path_params: dict):
    scope = {
        "type": "http", "method": "GET",
        "path_params": path_params, "headers": [], "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, json.loads(resp.body)


_RESULT = {
    "status": "done",
    "message": "agent run completed.",
    "agent_output": "x" * 50_000,  # the bulk that must NOT ride along
    "verify": {"ran": True, "cmd": "dotnet test", "passed": True,
               "exit_code": 0, "timed_out": False, "output": "Passed! 56"},
    "delivery": {"delivered": True, "branch": "feat/x", "committed": True,
                 "pushed": True, "pr_url": "https://github.com/o/r/pull/9",
                 "error": None},
    "diff_stats": {"files": 3, "insertions": 120, "deletions": 4},
    "usage": {"input_tokens": 700, "output_tokens": 24000,
              "cache_read_tokens": 2_800_000, "cost_usd": 1.46},
}

_TASK_ID = "12d1acd3-32f2-400e-b221-888a3c82b1be"


def _seed(store: StateStore, *, result_json: str | None = json.dumps(_RESULT)) -> None:
    store.create_task(
        id=_TASK_ID, kind="implement_feature",
        workspace_dir="/w", goal="Build the thing.\n\nACCEPTANCE: it works.",
        verify_cmd="dotnet test", deliver=True,
    )
    store.mark_running(_TASK_ID)
    if result_json is not None:
        store.mark_done(_TASK_ID, result_json, pr_url="https://github.com/o/r/pull/9")


def test_task_json_carries_contract_and_decomposed_verdicts(store):
    _seed(store)

    code, body = _get(http_mod.task_json, {"task_id": _TASK_ID})

    assert code == 200
    t = body["task"]
    assert t["id"] == _TASK_ID
    assert t["goal"] == "Build the thing.\n\nACCEPTANCE: it works."  # untruncated contract
    assert t["status"] == "done"
    assert t["prUrl"] == "https://github.com/o/r/pull/9"
    assert t["verifyCmd"] == "dotnet test"
    assert body["verify"]["passed"] is True
    assert body["delivery"]["branch"] == "feat/x"
    assert body["diffStats"] == {"files": 3, "insertions": 120, "deletions": 4}
    assert body["usage"]["cost_usd"] == 1.46
    # The transcript bulk stays in the events feed — never here.
    assert "agent_output" not in json.dumps(body)


def test_task_json_unsettled_task_has_null_verdicts(store):
    _seed(store, result_json=None)  # created + running, never settled

    code, body = _get(http_mod.task_json, {"task_id": _TASK_ID})

    assert code == 200
    assert body["task"]["status"] == "running"
    assert body["verify"] is None and body["delivery"] is None
    assert body["diffStats"] is None and body["usage"] is None


def test_task_json_corrupt_result_degrades_visibly_not_500(store):
    _seed(store, result_json="{not json at all")

    code, body = _get(http_mod.task_json, {"task_id": _TASK_ID})

    assert code == 200                      # loud is the 404 path; corrupt result
    assert body["task"]["goal"]             # still renders the contract
    assert body["verify"] is None
    assert body["resultCorrupt"] is True    # degraded VISIBLY, not silently


def test_task_json_unknown_id_is_404_and_bad_shape_400(store):
    code, _ = _get(http_mod.task_json, {"task_id": "a/../b"})
    assert code == 400                      # path-shaped ids are rejected outright

    _seed(store)
    code, _ = _get(http_mod.task_json, {"task_id": "12d1acd3-32f2-400e-b221-000000000000"})
    assert code == 404                      # a typo is not an empty task
