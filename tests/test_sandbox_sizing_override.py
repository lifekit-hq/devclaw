"""Per-project sandbox sizing (spec 020 US4 — ADR 0005's sibling).

A heavy frontend repo declares its own sandbox_memory/sandbox_cpus; every
other project rides the DEVCLAW_SANDBOX_MEMORY/_CPUS defaults. Chain under
test (copying test_sandbox_image_override wholesale): registry field →
write-time validation (grammar AND host admittability, clarified with Denys:
reject loudly, never store a value dispatch can only defer on forever) →
task-queue resolution → EngineRequest → the docker argv, where the SAME
resolved value feeds --memory/--memory-swap AND the declared env pair
(FR-007) → launch admission accounting (FR-010).
"""

from __future__ import annotations

import pytest

import devclaw.engine.sandcastle as sc
import devclaw.host_resources as host_resources
from devclaw.engine import EngineRequest
from devclaw.project_registry import ProjectRegistry


def _reg(tmp_path):
    return ProjectRegistry(str(tmp_path / "devclaw.db"))


@pytest.fixture(autouse=True)
def _big_host(monkeypatch):
    """Pin MemTotal to 64 GiB so write-time admittability is deterministic
    regardless of the dev/CI host's real RAM."""
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: 64 << 30)


# ---- registry field ----


def test_sandbox_sizing_persists_and_resolves(tmp_path):
    reg = _reg(tmp_path)
    reg.create(
        id="fe", name="FE", workspace_dir="/ws/fe",
        sandbox_memory="6g", sandbox_cpus="4.0",
    )
    reopened = ProjectRegistry(str(tmp_path / "devclaw.db"))
    p = reopened.get("fe")
    assert p.sandbox_memory == "6g" and p.sandbox_cpus == "4.0"
    assert reopened.resolve_override("fe", "sandbox_memory", None) == "6g"
    assert reopened.resolve_override("fe", "sandbox_cpus", None) == "4.0"
    assert reopened.resolve_override("other", "sandbox_memory", None) is None


def test_update_pins_and_clears_sizing_with_three_way_semantics(tmp_path):
    reg = _reg(tmp_path)
    reg.create(id="fe", name="FE", workspace_dir="/ws/fe")
    reg.update("fe", sandbox_memory="6g")
    assert reg.get("fe").sandbox_memory == "6g"
    reg.update("fe", notes="unrelated")  # omit = untouched
    assert reg.get("fe").sandbox_memory == "6g"
    reg.update("fe", sandbox_memory=None)  # explicit None = clear
    assert reg.get("fe").sandbox_memory is None


def test_registry_rejects_malformed_sizing_values(tmp_path):
    reg = _reg(tmp_path)
    reg.create(id="fe", name="FE", workspace_dir="/ws/fe")
    for bad in ("--memory=9g", "", "lots", "4gb", "-2g", "4 g"):
        with pytest.raises(ValueError, match="sandbox_memory"):
            reg.update("fe", sandbox_memory=bad)
    for bad in ("", "fast", "-1", "0"):
        with pytest.raises(ValueError, match="sandbox_cpus"):
            reg.update("fe", sandbox_cpus=bad)
    for good in ("6g", "3072m", "512M", "2048"):
        reg.update("fe", sandbox_memory=good)
        assert reg.get("fe").sandbox_memory == good


def test_registry_rejects_an_unadmittable_memory_loudly_at_write_time(tmp_path, monkeypatch):
    """The clarify ruling: a value the host can NEVER admit (value + cognition
    reserve > MemTotal) is refused at the write choke point with both numbers
    in the message — it must not be stored to wedge dispatch silently."""
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: 16 << 30)
    reg = _reg(tmp_path)
    reg.create(id="fe", name="FE", workspace_dir="/ws/fe")
    with pytest.raises(ValueError, match="can never be admitted"):
        reg.update("fe", sandbox_memory="15g")  # 15g + 1536m reserve > 16g
    assert reg.get("fe").sandbox_memory is None  # nothing stored
    reg.update("fe", sandbox_memory="8g")  # admittable value still accepted
    assert reg.get("fe").sandbox_memory == "8g"


def test_unreadable_memtotal_skips_admittability_but_keeps_grammar(tmp_path, monkeypatch):
    # Fail-open mirror of the admission brake: an unmeasurable host accepts
    # any well-formed value (grammar still enforced).
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: None)
    reg = _reg(tmp_path)
    reg.create(id="fe", name="FE", workspace_dir="/ws/fe", sandbox_memory="999g")
    with pytest.raises(ValueError, match="sandbox_memory"):
        reg.update("fe", sandbox_memory="not-a-size")


# ---- engine argv: enforcement AND declaration from ONE resolved value ----


def test_sandcastle_argv_enforces_and_declares_the_override(tmp_path):
    base = dict(
        container_name="c", host_bind_path="/host/ws",
        claude_dir="/home/me/.claude", payload="{}",
    )
    args = sc._build_docker_args(**base, sandbox_memory="6g", sandbox_cpus="4.0")
    assert args[args.index("--memory") + 1] == "6g"
    assert args[args.index("--memory-swap") + 1] == "6g"
    assert args[args.index("--cpus") + 1] == "4.0"
    assert "DEVCLAW_SANDBOX_MEMORY=6g" in args
    assert "DEVCLAW_SANDBOX_CPUS=4.0" in args
    default = sc._build_docker_args(**base)
    assert default[default.index("--memory") + 1] == sc.SANDBOX_MEMORY
    assert f"DEVCLAW_SANDBOX_MEMORY={sc.SANDBOX_MEMORY}" in default


# ---- dispatch wiring ----


def test_dispatch_resolves_the_owning_projects_sizing(tmp_path):
    from devclaw.task_queue import TaskQueue

    reg = _reg(tmp_path)
    reg.create(
        id="fe", name="FE", workspace_dir=str(tmp_path / "ws"),
        sandbox_memory="6g", sandbox_cpus="4.0",
    )
    q = TaskQueue.__new__(TaskQueue)
    q._registry = reg
    assert q._sandbox_sizing("fe") == ("6g", "4.0")
    assert q._sandbox_sizing("unregistered") == (None, None)
    # admission accounts the OVERRIDE, not the default (FR-010)
    assert q._effective_sandbox_mem_bytes("fe") == 6 << 30
    from devclaw.queue.admission import SANDBOX_MEMORY_BYTES
    assert q._effective_sandbox_mem_bytes("unregistered") == SANDBOX_MEMORY_BYTES
    q._registry = None
    assert q._sandbox_sizing("fe") == (None, None)


def test_engine_request_defaults_to_no_sizing_override():
    req = EngineRequest(kind="implement_feature", workspace_dir="/ws", goal="g")
    assert req.sandbox_memory is None and req.sandbox_cpus is None
