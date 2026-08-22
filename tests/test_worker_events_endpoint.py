"""The worker-execution trace surface — the mirror of the #455 cognition
transcript reader, one layer down at the worker.

  * ``devclaw.server.worker_events.decode_event`` — turns a raw worker
    event (as stored in the events table) into a readable
    {kind, title, summary, detail, raw} row.
  * ``GET /tasks/{task_id}/events.json`` — reads a settled task's full turn log
    back POST-HOC and decodes it (the SSE /goals/{id}/events stream is live-only).

The structural point (structural-root-2026-08-05): the worker's execution was
already CAPTURED (one events row per turn) but never SURFACED as readable content
— the old console decoded a UI mock's field names and showed "MessageEvent" blips.
These tests pin that the REAL payload shapes (llm_message text / action tool+cmd /
observation output) come through as content, and that the full raw payload is
never hidden (the #455 untruncated guarantee).
"""

from __future__ import annotations

import asyncio
import json

from starlette.requests import Request

import devclaw.server.routes.tasks as tasks_routes
from devclaw.server.worker_events import decode_event
from devclaw.state_store import StateStore


# realistic inner model_dump payloads (what task_queue stores as payload_json)
_MSG = {"llm_message": {"content": [
    {"type": "text", "text": "I'll add the /health endpoint and a test for it."},
]}}
_ACTION = {"thought": "run the test suite to see the baseline",
           "action": {"tool": "execute_bash", "command": "dotnet test"}}
_OBS = {"content": "Passed!  - Failed: 0, Passed: 20, Skipped: 0"}
# The ACP path's dominant event (Claude Code): a tool call with a human ``title``,
# ``raw_input`` args, and the OUTPUT nested under ``content`` — the real shape the
# runner stores. Matches runner's ACPToolCallEvent model_dump.
_ACP_TOOLCALL = {
    "id": "be733681", "timestamp": "2026-08-07T09:27:00", "source": "agent",
    "tool_call_id": "toolu_015KC2um", "status": "completed", "is_error": False,
    "title": "Read src/FieldNotes.Api/Program.cs",
    "raw_input": {"path": "src/FieldNotes.Api/Program.cs"},
    "content": [{"type": "content", "content": {
        "annotations": None, "field_meta": None,
        "text": "var builder = WebApplication.CreateBuilder(args);"}}],
    "kind": "ACPToolCallEvent",
}


def _seed_task(store: StateStore, task_id: str = "t1") -> None:
    store.append_event(task_id=task_id, program_id=None, type="MessageEvent",
                       source="agent", payload_json=json.dumps(_MSG))
    store.append_event(task_id=task_id, program_id=None, type="ActionEvent",
                       source="agent", payload_json=json.dumps(_ACTION))
    store.append_event(task_id=task_id, program_id=None, type="ObservationEvent",
                       source="environment", payload_json=json.dumps(_OBS))


def _get(fn, path_params: dict, query: bytes = b""):
    scope = {
        "type": "http", "method": "GET",
        "path_params": path_params, "headers": [], "query_string": query,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, json.loads(resp.body)


# ---- the decoder (pure) ----------------------------------------------------


def test_decode_message_event_extracts_agent_text():
    row = decode_event(_ev(1, "MessageEvent", "agent", _MSG))
    assert row["kind"] == "message"
    assert "add the /health endpoint" in row["detail"]     # real content, not a blip
    assert row["title"] == "agent message"
    assert row["raw"] == _MSG                                # full payload never hidden


def test_decode_action_event_extracts_tool_and_command():
    row = decode_event(_ev(2, "ActionEvent", "agent", _ACTION))
    assert row["kind"] == "action"
    assert "execute_bash" in row["title"]
    assert "dotnet test" in row["detail"]
    assert "run the test suite" in row["detail"]            # the thought comes through


def test_decode_observation_event_extracts_output():
    row = decode_event(_ev(3, "ObservationEvent", "environment", _OBS))
    assert row["kind"] == "observation"
    assert "Passed: 20" in row["detail"]


def test_decode_acp_tool_call_extracts_human_title_and_output_not_raw_json():
    # Regression: the ACP worker (Claude Code) emits ACPToolCallEvent, whose type
    # matches none of message/action/observation — it fell through to "other" and
    # dumped raw JSON into the Execution trace (the console showed rows like
    # ``ACPToolCallEvent { "content": [ ... ] }``). Now it reads the human ``title``
    # and the tool OUTPUT out of ``content``.
    row = decode_event(_ev(1, "ACPToolCallEvent", "agent", _ACP_TOOLCALL))
    assert row["kind"] == "action"
    assert row["title"] == "Read src/FieldNotes.Api/Program.cs"   # human, not "ACPToolCallEvent"
    assert "WebApplication.CreateBuilder" in row["detail"]        # the tool output surfaced
    assert row["summary"].startswith("var builder")              # summary previews output, not JSON
    assert "ACPToolCallEvent" not in row["summary"]
    assert row["raw"] == _ACP_TOOLCALL                            # full payload still preserved


def test_decode_acp_tool_call_error_is_kind_error():
    payload = {**_ACP_TOOLCALL, "is_error": True,
               "content": [{"content": {"text": "dotnet: command not found"}}]}
    row = decode_event(_ev(2, "ACPToolCallEvent", "agent", payload))
    assert row["kind"] == "error"
    assert "command not found" in row["detail"]


def test_decode_acp_pending_tool_call_has_no_raw_json_dump():
    # A just-initiated call: content null, no args yet (the SDK emits pending →
    # completed per call). The human title carries it; we must NOT dump raw JSON
    # into the trace — that was the exact noise the fix removes. raw stays intact.
    pending = {"tool_call_id": "toolu_x", "title": "Find `**/*.md`",
               "status": "pending", "content": None, "kind": "ACPToolCallEvent"}
    row = decode_event(_ev(3, "ACPToolCallEvent", "agent", pending))
    assert row["kind"] == "action"
    assert row["title"] == "Find `**/*.md`"
    assert "{" not in row["detail"]            # no JSON blob leaked into the trace
    assert row["raw"] == pending               # full payload still preserved


def test_decode_unknown_event_falls_back_to_full_dump_never_blank():
    weird = {"mystery": {"nested": [1, 2, 3]}, "note": "schema drift"}
    row = decode_event(_ev(9, "SomeFutureEvent", "agent", weird))
    assert row["kind"] == "other"
    # nothing is hidden — the whole payload is in detail AND raw
    assert "schema drift" in row["detail"]
    assert row["raw"] == weird


def test_decode_tolerates_corrupt_payload_json():
    ev = _RawEv(id=1, type="MessageEvent", source="agent", payload_json="{not json",
                ts=1)
    row = decode_event(ev)  # must not raise
    assert row["kind"] == "message"


# ---- the endpoint ----------------------------------------------------------


def test_endpoint_returns_decoded_turn_by_turn_trace(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "s.db"))
    _seed_task(store)
    monkeypatch.setattr(tasks_routes, "store", store)

    status, body = _get(tasks_routes.task_events_json, {"task_id": "t1"})
    assert status == 200
    assert body["count"] == 3
    kinds = [r["kind"] for r in body["events"]]
    assert kinds == ["message", "action", "observation"]     # in emission order
    assert "add the /health endpoint" in body["events"][0]["detail"]
    assert "dotnet test" in body["events"][1]["detail"]
    assert body["nextCursor"] is None                        # fewer than the page limit


def test_endpoint_empty_task_is_empty_not_404(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "s.db"))
    monkeypatch.setattr(tasks_routes, "store", store)

    status, body = _get(tasks_routes.task_events_json, {"task_id": "never-ran"})
    assert status == 200
    assert body == {"events": [], "count": 0, "nextCursor": None}


def test_endpoint_paginates_with_since_and_limit(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "s.db"))
    _seed_task(store)
    monkeypatch.setattr(tasks_routes, "store", store)

    status, body = _get(tasks_routes.task_events_json, {"task_id": "t1"}, query=b"limit=2")
    assert status == 200
    assert body["count"] == 2
    assert body["nextCursor"] is not None                    # more remain → cursor set
    status2, body2 = _get(
        tasks_routes.task_events_json, {"task_id": "t1"},
        query=f"since={body['nextCursor']}".encode(),
    )
    assert body2["count"] == 1
    assert body2["events"][0]["kind"] == "observation"


def test_endpoint_rejects_malformed_task_id(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "s.db"))
    monkeypatch.setattr(tasks_routes, "store", store)

    status, body = _get(tasks_routes.task_events_json, {"task_id": "../etc/passwd"})
    assert status == 400
    assert body["error"] == "bad_task_id"


# ---- tiny event doubles ----------------------------------------------------


class _RawEv:
    def __init__(self, *, id, type, source, payload_json, ts):
        self.id = id
        self.type = type
        self.source = source
        self.payload_json = payload_json
        self.ts = ts


def _ev(id: int, type: str, source: str, payload: dict) -> _RawEv:
    return _RawEv(id=id, type=type, source=source, payload_json=json.dumps(payload), ts=id)
