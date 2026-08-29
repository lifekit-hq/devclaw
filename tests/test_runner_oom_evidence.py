"""In-sandbox OOM evidence capture (spec 020 US1).

The runner shares the container cgroup with the agent and survives the
agent's OOM death (proven 2026-08-26: the runner is what reported the
failure), so IT reads the kernel's `oom_kill` counter and stamps the
`sandbox OOM-killed (cap=…, oom_kill=…)` marker the queue's settle path
classifies on. Contract: specs/020-sandbox-oom-legibility/contracts/
runner-oom-marker.md. Conservative by construction: no evidence ⇒ the error
text passes through byte-identical (exit-137 alone is any SIGKILL, never
proof).
"""

import importlib.util
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_oom", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level import; the runner is stdlib-only (spec 011)
    return mod


def _fake_cgroup(tmp_path, monkeypatch, runner, *, oom_kill, mem_max="2147483648"):
    events = tmp_path / "memory.events"
    events.write_text(f"low 0\nhigh 4\nmax 12\noom 1\noom_kill {oom_kill}\n")
    mmax = tmp_path / "memory.max"
    mmax.write_text(f"{mem_max}\n")
    monkeypatch.setattr(runner, "_CGROUP_MEMORY_EVENTS", str(events))
    monkeypatch.setattr(runner, "_CGROUP_MEMORY_MAX", str(mmax))
    return events


def test_oom_annotate_stamps_marker_on_new_kill(tmp_path, monkeypatch, runner):
    # This case pins the CGROUP-derived cap — the fallback when the engine
    # declared no cap. The env MUST be cleared: inside a real devclaw sandbox
    # the engine always declares DEVCLAW_SANDBOX_MEMORY (FR-007, next test),
    # so without this the test fails in any sandbox running a per-project
    # memory override (found 2026-08-29: the first 6g devclaw sandbox failed
    # its verify gate on exactly this line, and the failure text containing
    # "sandbox OOM-killed" then got the settle misread as a real OOM).
    monkeypatch.delenv("DEVCLAW_SANDBOX_MEMORY", raising=False)
    events = _fake_cgroup(tmp_path, monkeypatch, runner, oom_kill=0)
    baseline = runner._read_oom_kill_count()
    assert baseline == 0
    events.write_text("low 0\nhigh 4\nmax 12\noom 2\noom_kill 1\n")
    out = runner._oom_annotate("agent process exited unexpectedly", baseline)
    assert out.startswith("sandbox OOM-killed (cap=2147483648 bytes, oom_kill=1): ")
    assert out.endswith("agent process exited unexpectedly")


def test_oom_annotate_prefers_the_engine_declared_cap(tmp_path, monkeypatch, runner):
    # FR-007 single-source: when the engine declared the cap into the env,
    # the label uses THAT value (the launch parameter), not a re-derivation.
    events = _fake_cgroup(tmp_path, monkeypatch, runner, oom_kill=3)
    monkeypatch.setenv("DEVCLAW_SANDBOX_MEMORY", "2g")
    baseline = 3
    events.write_text("oom_kill 4\n")
    out = runner._oom_annotate("boom", baseline)
    assert out.startswith("sandbox OOM-killed (cap=2g, oom_kill=1): ")


def test_no_new_kill_passes_error_through_byte_identical(tmp_path, monkeypatch, runner):
    _fake_cgroup(tmp_path, monkeypatch, runner, oom_kill=5)
    assert runner._oom_annotate("plain failure", 5) == "plain failure"


def test_unreadable_cgroup_means_no_evidence_and_no_crash(tmp_path, monkeypatch, runner):
    # Missing files (cgroup v1 host, or a future layout change) degrade to
    # "no evidence" — never a raise, never a false positive.
    monkeypatch.setattr(runner, "_CGROUP_MEMORY_EVENTS", str(tmp_path / "absent"))
    monkeypatch.setattr(runner, "_CGROUP_MEMORY_MAX", str(tmp_path / "absent2"))
    assert runner._read_oom_kill_count() is None
    assert runner._oom_annotate("agent died", None) == "agent died"
    assert runner._oom_annotate("agent died", 7) == "agent died"
