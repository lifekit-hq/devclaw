"""Cognition-timeout OBSERVABILITY — regression tests (2026-07-24).

A ``claude --print`` timeout used to record only the opaque token ``"timeout"``:
we could not tell whether the CLI hung before emitting a single byte (auth /
network / queue — an *infra* stall) or streamed a genuinely slow/large
generation (a *work-size* problem). Those two causes need OPPOSITE fixes, so
picking one blind was guesswork.

``call_claude`` now reads a real subprocess as a stream, so a timeout captures,
on the trace + one greppable ``devclaw.cognition.timeout`` stderr line:

  * ``input_chars``   — prompt size (correlate timeouts with big inputs)
  * ``bytes_out``     — stdout produced before the kill (0 ⇒ hung before token)
  * ``first_byte_ms`` — when the first stdout byte arrived (``none`` ⇒ never)

The raised ``PlannerError`` wording is UNCHANGED (the quota classifier keys off
it), and test doubles that implement only ``.communicate()`` keep working via
the buffered fallback (covered by tests/test_cognition_timeout.py).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from devclaw import llm_call


def _fake_claude(tmp_path: Path, body: str) -> Path:
    """A minimal executable stand-in for the ``claude`` CLI: it drains stdin
    (the prompt) then runs ``body``. Real subprocess → exercises the streaming
    read path, unlike the in-process ``.communicate()`` doubles elsewhere."""
    script = tmp_path / "fake-claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, time  # noqa\n"
        "sys.stdin.buffer.read()\n"
        f"{body}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


async def test_timeout_on_silent_hang_records_zero_bytes_and_no_first_byte(
    tmp_path, monkeypatch, capsys
):
    """A CLI that consumes the prompt then emits nothing before the deadline →
    ``bytes_out=0`` / ``first_byte_ms=none``: the 'hung before first token'
    signature (an infra stall, NOT slow generation)."""
    fake = _fake_claude(tmp_path, "time.sleep(5)")
    monkeypatch.setattr(llm_call, "CLAUDE_BIN", str(fake))
    # single-attempt: this pins the timeout DIAGNOSTIC, not the retry policy
    # (transient retry-on-timeout is covered in tests/test_cognition_retry.py).
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 0)

    with pytest.raises(llm_call.PlannerError) as ei:
        await llm_call.call_claude("hello", role="review", timeout_ms=400)

    # PlannerError wording is unchanged (quota classifier invariant).
    assert "claude --print timed out after 400ms" in str(ei.value)
    err = capsys.readouterr().err
    assert "devclaw.cognition.timeout" in err
    assert "role=review" in err
    assert "input_chars=5" in err       # len("hello")
    assert "bytes_out=0" in err
    assert "first_byte_ms=none" in err


async def test_timeout_after_partial_output_records_bytes_and_first_byte(
    tmp_path, monkeypatch, capsys
):
    """A CLI that emits stdout then stalls past the deadline → ``bytes_out>0``
    and a numeric ``first_byte_ms``: the 'slow/large generation' signature,
    distinct from the silent hang above. This is the discriminator the old
    bare ``"timeout"`` threw away."""
    fake = _fake_claude(
        tmp_path,
        "sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(5)",
    )
    monkeypatch.setattr(llm_call, "CLAUDE_BIN", str(fake))
    # single-attempt: pins the timeout DIAGNOSTIC, not retry (see the sibling test).
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 0)

    with pytest.raises(llm_call.PlannerError):
        await llm_call.call_claude("hello", role="review", timeout_ms=1000)

    err = capsys.readouterr().err
    assert "devclaw.cognition.timeout" in err
    assert "bytes_out=7" in err            # the 7 bytes of "partial"
    assert "first_byte_ms=none" not in err  # a first byte WAS seen


async def test_streaming_happy_path_returns_envelope_text(tmp_path, monkeypatch):
    """The streaming read path (now used for every real subprocess) still parses
    the ``--output-format json`` envelope and returns its result text on a clean
    exit — proving the rewrite didn't regress the success path."""
    body = (
        "import json\n"
        "sys.stdout.write(json.dumps({'type': 'result', 'subtype': 'success', "
        "'result': 'ok', 'usage': {'input_tokens': 3, 'output_tokens': 2}}))"
    )
    fake = _fake_claude(tmp_path, body)
    monkeypatch.setattr(llm_call, "CLAUDE_BIN", str(fake))

    out = await llm_call.call_claude("hello", role="review", timeout_ms=5000)
    assert out == "ok"
