"""The silence watchdog and the resume seam of the ``claude -p`` engine (#952).

Liveness is the stream, never a clock: a session that never emits a first
frame, or goes silent mid-stream, is killed and handed back as an error the
queue settles ``INTERRUPTED`` — never left hanging on the quota. The session id
is reported the moment the stream names it, and a resume id the agent no
longer knows falls back to a fresh session instead of failing the task.

Driven with a fake ``claude`` script (a python file whose basename is not
``claude``, so the conftest cognition guard admits it) — no real cognition.
"""

import sys
import time

import pytest

from devclaw import config as _config
from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

_FAKE = r'''
import json, os, sys, time
mode = os.environ["FAKE_CLAUDE_MODE"]
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
def emit(frame):
    print(json.dumps(frame), flush=True)
resuming = "--resume" in sys.argv
if mode == "silent":
    time.sleep(60)
elif mode == "stall":
    emit({"type": "system", "subtype": "init", "session_id": "sess-stall"})
    time.sleep(60)
elif mode == "resume_missing" and resuming:
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
          "errors": ["No conversation found with session ID: gone"]})
else:
    emit({"type": "system", "subtype": "init", "session_id": "sess-new"})
    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": "NOTHING: nothing to do", "session_id": "sess-new",
          "usage": {"input_tokens": 3, "output_tokens": 2}})
'''


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fakeclaude.py"
    script.write_text(_FAKE)
    wrapper = tmp_path / "fakeclaude"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(_config, "CLAUDE_BIN", str(wrapper))
    monkeypatch.setattr(_config, "EXEC_MODEL", "")
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def use(mode: str, *, first: float = 30, silence: float = 30):
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
        monkeypatch.setattr(_config, "FIRST_FRAME_S", first)
        monkeypatch.setattr(_config, "SILENCE_S", silence)
        return log
    return use


def _req(tmp_path, **kw) -> EngineRequest:
    return EngineRequest(kind="review_repository", workspace_dir=str(tmp_path), goal="review", **kw)


async def test_no_first_frame_is_killed_early_never_waits_out_the_silence_bound(fake_claude, tmp_path):
    fake_claude("silent", first=1, silence=600)
    t0 = time.monotonic()
    out = await engine.run_claude(_req(tmp_path))
    assert time.monotonic() - t0 < 20
    assert out["status"] == "error"
    assert "no first frame" in out["error"]


async def test_mid_stream_silence_is_killed_and_keeps_the_session_for_resume(fake_claude, tmp_path):
    fake_claude("stall", first=30, silence=1)
    seen: list[str] = []
    t0 = time.monotonic()
    out = await engine.run_claude(_req(tmp_path, on_session=seen.append))
    assert time.monotonic() - t0 < 20
    assert out["status"] == "error"
    assert "silent" in out["error"]
    assert out["session_id"] == "sess-stall"
    assert seen == ["sess-stall"]


async def test_an_unknown_resume_id_falls_back_to_a_fresh_session(fake_claude, tmp_path):
    log = fake_claude("resume_missing")
    seen: list[str] = []
    out = await engine.run_claude(_req(tmp_path, resume_session_id="gone", on_session=seen.append))
    assert out["status"] == "ok"
    assert out["session_id"] == "sess-new"
    assert seen == ["sess-new"]
    calls = [line for line in log.read_text().splitlines() if line]
    assert len(calls) == 2
    assert '"--resume"' in calls[0] and '"--resume"' not in calls[1]
