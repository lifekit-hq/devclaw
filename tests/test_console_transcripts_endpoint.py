"""Regression tests for the console cognition-transcript read surface:

  * ``GET /goals/{goal_id}/transcripts.json`` — an index (one row per
    ``claude --print`` call) with size/cost/error metadata.
  * ``GET /goals/{goal_id}/transcripts/{filename}`` — the FULL prompt +
    response of one call, NO truncation.

Fixtures are generated with the REAL writer
(``PersistentTracer.write_transcript``) — the same guarantee tests/test_trace_view.py
makes — so the endpoint's wire format can never drift from what production
actually writes to disk. Two load-bearing assertions:

  * a >100 KB prompt round-trips through the endpoint BYTE-FOR-BYTE (the reader
    exists precisely to surface the prompt sizes that OOM/timeout cognition — a
    truncating endpoint would defeat its own purpose); and
  * path-traversal filenames are rejected, never serving a file outside the
    goal's transcripts dir (this route reads arbitrary filenames off the disk).
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

from starlette.requests import Request

import devclaw.server.http as http_mod
from devclaw.loom.trace import PersistentTracer


def _write(goals_dir: Path, goal_id: str, role: str, prompt: str, response: str,
           **kw) -> str:
    """Emit one real transcript via the production writer (store unused — it only
    touches goals_dir + goal_id). Returns the basename it wrote."""
    tracer = PersistentTracer(
        store=None, trace_id="trace-1", goal_id=goal_id, goals_dir=goals_dir
    )
    name = tracer.write_transcript(
        role=role, model=kw.get("model", "claude-sonnet-4-6"),
        prompt=prompt, response=response,
        tokens_in=kw.get("tokens_in"), tokens_out=kw.get("tokens_out"),
        cost_usd=kw.get("cost_usd"), error=kw.get("error", ""),
    )
    time.sleep(0.002)  # distinct-ms stamps ⇒ filename order == write order
    return name


def _point_goals_at(monkeypatch, goals_dir: Path) -> None:
    """Make the http module's ``goals`` service resolve to ``goals_dir`` — the
    routes read ``goals._cfg.goals_dir`` exactly as the live GoalService does."""
    fake = types.SimpleNamespace(_cfg=types.SimpleNamespace(goals_dir=goals_dir))
    monkeypatch.setattr(http_mod, "goals", fake)


def _get(fn, path_params: dict):
    scope = {
        "type": "http",
        "method": "GET",
        "path_params": path_params,
        "headers": [],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, json.loads(resp.body)


def _seed_run(goals_dir: Path, goal_id: str = "g1") -> str:
    """A small planner call, a HUGE decomposer call, and an errored evaluator
    call. Returns the sentinel buried at the tail of the huge prompt."""
    sentinel = "DECOMPOSE_TAIL_SENTINEL"
    huge_prompt = ("goal history line\n" * 6000) + sentinel  # ~110 KB, sentinel last
    _write(goals_dir, goal_id, "goal_planner", "pick the next action", "{next: 'x'}",
           tokens_in=10, tokens_out=5, cost_usd=0.001)
    _write(goals_dir, goal_id, "goal_decomposer", huge_prompt, "- [ ] step one",
           tokens_in=27000, tokens_out=8)
    _write(goals_dir, goal_id, "evaluator", "is it done?", "",
           error="claude --print exited -9")
    return sentinel


# ── index ────────────────────────────────────────────────────────────────────

def test_transcripts_index_lists_every_call_with_metadata(tmp_path, monkeypatch):
    goals_dir = tmp_path / "goals"
    _seed_run(goals_dir)
    _point_goals_at(monkeypatch, goals_dir)

    status, body = _get(http_mod.goal_transcripts_json, {"goal_id": "g1"})
    assert status == 200
    rows = body["transcripts"]
    assert body["count"] == 3 and len(rows) == 3
    # Oldest first, seq 1..N, roles in write order.
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert [r["role"] for r in rows] == ["goal_planner", "goal_decomposer", "evaluator"]
    decomposer = rows[1]
    assert decomposer["promptChars"] > 100_000       # the OOM/timeout size class
    assert decomposer["tokensIn"] == "27000"
    assert decomposer["model"] == "claude-sonnet-4-6"
    assert decomposer["filename"].endswith("-goal_decomposer.md")
    # The errored evaluator call is flagged, not hidden.
    assert rows[2]["error"] == "claude --print exited -9"


def test_transcripts_index_empty_for_goal_without_transcripts(tmp_path, monkeypatch):
    """A known goal that never ran cognition returns an empty index (200), not a
    404 — the console links here from a goal that may have no calls yet."""
    _point_goals_at(monkeypatch, tmp_path / "goals")
    status, body = _get(http_mod.goal_transcripts_json, {"goal_id": "never-ran"})
    assert status == 200 and body == {"transcripts": [], "count": 0}


def test_transcripts_index_ignores_generated_view_files(tmp_path, monkeypatch):
    """A goal dir that exists but has NO transcripts/ subdir must return an empty
    index — never glob its generated VIEW files (STATUS.md/log.md/inbox.md) as
    bogus cognition rows."""
    goals_dir = tmp_path / "goals"
    gdir = goals_dir / "g-views"
    gdir.mkdir(parents=True)
    (gdir / "STATUS.md").write_text("# status view")
    (gdir / "log.md").write_text("# log view")
    _point_goals_at(monkeypatch, goals_dir)
    status, body = _get(http_mod.goal_transcripts_json, {"goal_id": "g-views"})
    assert status == 200 and body == {"transcripts": [], "count": 0}


def test_transcripts_index_rejects_traversal_goal_id(tmp_path, monkeypatch):
    _point_goals_at(monkeypatch, tmp_path / "goals")
    status, body = _get(http_mod.goal_transcripts_json, {"goal_id": "../../etc"})
    assert status == 400 and body["error"] == "bad_goal_id"


# ── full one — the payoff: no truncation ───────────────────────────────────────

def test_transcript_full_roundtrips_huge_prompt_untruncated(tmp_path, monkeypatch):
    goals_dir = tmp_path / "goals"
    sentinel = _seed_run(goals_dir)
    _point_goals_at(monkeypatch, goals_dir)

    # Discover the decomposer's filename via the index, then pull it in full.
    _, index = _get(http_mod.goal_transcripts_json, {"goal_id": "g1"})
    fname = next(r["filename"] for r in index["transcripts"] if r["role"] == "goal_decomposer")

    status, body = _get(http_mod.goal_transcript_full, {"goal_id": "g1", "filename": fname})
    assert status == 200
    assert body["role"] == "goal_decomposer"
    assert body["promptChars"] > 100_000
    # The whole point: the full prompt survives, tail and all — no truncation.
    assert body["prompt"].endswith(sentinel)
    assert body["prompt"].count("goal history line") == 6000
    assert body["response"] == "- [ ] step one"


def test_transcript_full_carries_error_and_empty_response(tmp_path, monkeypatch):
    goals_dir = tmp_path / "goals"
    _seed_run(goals_dir)
    _point_goals_at(monkeypatch, goals_dir)
    _, index = _get(http_mod.goal_transcripts_json, {"goal_id": "g1"})
    fname = next(r["filename"] for r in index["transcripts"] if r["role"] == "evaluator")
    status, body = _get(http_mod.goal_transcript_full, {"goal_id": "g1", "filename": fname})
    assert status == 200
    assert body["error"] == "claude --print exited -9"
    assert body["response"] == ""  # the call produced no response


# ── path-traversal guard — the security-critical assertions ────────────────────

def test_transcript_full_rejects_path_traversal(tmp_path, monkeypatch):
    goals_dir = tmp_path / "goals"
    _seed_run(goals_dir)
    _point_goals_at(monkeypatch, goals_dir)
    # A secret .md sitting OUTSIDE the goal's transcripts dir. No crafted filename
    # may return its bytes.
    secret = tmp_path / "secret.md"
    secret.write_text("TOP_SECRET_SHOULD_NEVER_LEAK")

    for bad in [
        "../../secret.md",           # climb out with ..
        "../secret.md",
        "nested/foo.md",             # a path separator
        "..\\secret.md",             # windows-style separator
        "transcript.txt",            # not markdown
        "",                          # empty
    ]:
        status, body = _get(
            http_mod.goal_transcript_full, {"goal_id": "g1", "filename": bad}
        )
        assert status in (400, 404), f"{bad!r} was not rejected"
        assert "TOP_SECRET_SHOULD_NEVER_LEAK" not in json.dumps(body)


def test_transcript_full_unknown_bare_filename_is_404(tmp_path, monkeypatch):
    goals_dir = tmp_path / "goals"
    _seed_run(goals_dir)
    _point_goals_at(monkeypatch, goals_dir)
    status, body = _get(
        http_mod.goal_transcript_full,
        {"goal_id": "g1", "filename": "2020-01-01T00:00:00-nope.md"},
    )
    assert status == 404 and body["error"] == "not_found"


def test_transcript_full_rejects_traversal_goal_id(tmp_path, monkeypatch):
    _point_goals_at(monkeypatch, tmp_path / "goals")
    status, body = _get(
        http_mod.goal_transcript_full, {"goal_id": "..", "filename": "x.md"}
    )
    assert status == 400 and body["error"] == "bad_goal_id"
