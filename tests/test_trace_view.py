"""Regression tests for the cognition transcript reader (devclaw/trace_view.py).

The reader parses the ``.md`` files PersistentTracer writes. To guarantee it can
never drift from what production actually emits, these tests generate their
fixtures with the REAL writer (``PersistentTracer.write_transcript``) rather than
hand-rolled markdown — if the on-disk format changes, this test breaks with it.

The load-bearing guarantee is the last assertion of the first test: a 100 KB
decomposer prompt (exactly the size class that OOMs / times out cognition) must
round-trip through the reader BYTE-FOR-BYTE. A reader that truncated the prompt
would defeat its own purpose — you'd be blind to the thing you opened it to see.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from devclaw import trace_view
from devclaw.loom.trace import PersistentTracer


def _write(goals_dir: Path, goal_id: str, role: str, prompt: str, response: str,
           **kw) -> str:
    """Emit one real transcript via the production writer. ``store`` is unused by
    write_transcript (it only touches goals_dir + goal_id), so a None store is
    fine — we're exercising the file path, not the sqlite mirror."""
    tracer = PersistentTracer(
        store=None, trace_id="trace-1", goal_id=goal_id, goals_dir=goals_dir
    )
    name = tracer.write_transcript(
        role=role, model=kw.get("model", "claude-sonnet-4-6"),
        prompt=prompt, response=response,
        tokens_in=kw.get("tokens_in"), tokens_out=kw.get("tokens_out"),
        cost_usd=kw.get("cost_usd"), error=kw.get("error", ""),
    )
    # Distinct-millisecond stamps so filename order == write order deterministically.
    time.sleep(0.002)
    return name


def _seed_run(goals_dir: Path, goal_id: str = "g1") -> str:
    """A realistic 3-call run: a small planner call, a HUGE decomposer call, and
    an evaluator call that errored. Returns the sentinel buried at the tail of
    the huge prompt so a test can prove it survived."""
    sentinel = "DECOMPOSE_TAIL_SENTINEL"
    huge_prompt = ("goal history line\n" * 6000) + sentinel  # ~110 KB, sentinel last
    _write(goals_dir, goal_id, "goal_planner", "pick the next action", "{next: 'x'}",
           tokens_in=10, tokens_out=5, cost_usd=0.001)
    _write(goals_dir, goal_id, "goal_decomposer", huge_prompt, "- [ ] step one",
           tokens_in=27000, tokens_out=8)
    _write(goals_dir, goal_id, "evaluator", "is it done?", "",
           error="claude --print exited -9")
    return sentinel


def test_trace_view_indexes_run_and_dumps_full_huge_prompt(tmp_path: Path) -> None:
    sentinel = _seed_run(tmp_path)

    calls = trace_view.load_dir(tmp_path, goal="g1")
    assert [c.role for c in calls] == ["goal_planner", "goal_decomposer", "evaluator"]

    decomposer = calls[1]
    assert decomposer.prompt_chars > 100_000  # the OOM/timeout size class
    assert decomposer.tokens_in == "27000"
    assert decomposer.model == "claude-sonnet-4-6"

    # The whole point: the full prompt survives, tail and all — no truncation.
    assert decomposer.prompt.endswith(sentinel)
    dump = trace_view.format_show(decomposer, seq=2)
    assert sentinel in dump
    assert "goal_decomposer" in dump

    # The index is scannable and honest about size + errors.
    index = trace_view.format_index(calls)
    assert "goal_decomposer" in index
    assert "110" in index or "k" in index  # human-sized prompt column
    assert "ERR" in index                   # the errored evaluator call is flagged
    assert "1 with errors" in index


def test_trace_view_filters_by_error_and_role(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    calls = trace_view.load_dir(tmp_path, goal="g1")

    errored = trace_view._apply_filters(calls, role=None, errors_only=True, grep=None)
    assert [c.role for c in errored] == ["evaluator"]
    assert errored[0].error == "claude --print exited -9"

    only_decomp = trace_view._apply_filters(calls, role="goal_decomposer",
                                            errors_only=False, grep=None)
    assert len(only_decomp) == 1

    grepped = trace_view._apply_filters(calls, role=None, errors_only=False,
                                        grep="DECOMPOSE_TAIL_SENTINEL")
    assert [c.role for c in grepped] == ["goal_decomposer"]


def test_trace_view_resolves_transcripts_goal_and_goaldir_paths(tmp_path: Path) -> None:
    _seed_run(tmp_path, goal_id="gX")
    # goals dir + --goal
    assert len(trace_view.load_dir(tmp_path, goal="gX")) == 3
    # goal dir directly (contains transcripts/)
    assert len(trace_view.load_dir(tmp_path / "gX")) == 3
    # the transcripts/ dir directly
    assert len(trace_view.load_dir(tmp_path / "gX" / "transcripts")) == 3
    # a missing goal is a clean error, not a crash
    with pytest.raises(FileNotFoundError):
        trace_view.load_dir(tmp_path, goal="nope")


def test_trace_view_cli_show_and_out_of_range(tmp_path: Path, capsys) -> None:
    _seed_run(tmp_path)
    tdir = str(tmp_path / "g1" / "transcripts")

    assert trace_view.main([tdir, "--show", "2"]) == 0
    assert "DECOMPOSE_TAIL_SENTINEL" in capsys.readouterr().out

    # --show past the end fails loud with a nonzero code, never an index error.
    assert trace_view.main([tdir, "--show", "99"]) == 2

    # bare invocation prints the index
    assert trace_view.main([tdir]) == 0
    assert "cognition calls" in capsys.readouterr().out


def test_trace_view_empty_dir_is_graceful(tmp_path: Path) -> None:
    (tmp_path / "transcripts").mkdir()
    assert trace_view.load_dir(tmp_path) == []
    assert "no transcripts" in trace_view.format_index([])


def test_trace_view_survives_broken_pipe(tmp_path: Path, monkeypatch) -> None:
    """Piping a big --show dump to `head`/`less` closes the pipe early; the tool
    must exit clean (0), never spew a BrokenPipeError traceback."""
    _seed_run(tmp_path)
    tdir = str(tmp_path / "g1" / "transcripts")

    class _ClosedPipe:
        def write(self, *_a):
            raise BrokenPipeError(32, "Broken pipe")
        def flush(self):
            pass

    monkeypatch.setattr("sys.stdout", _ClosedPipe())
    # Must not raise — the pipe break is swallowed and we still return 0.
    assert trace_view.main([tdir, "--show", "2"]) == 0
