"""Regression tests for the fan-out planner (spec 010 US3, FR-101/FR-104/FR-105).

The executor never decides to parallelise anything — it reads a decision the
plan already made. These tests pin what the planner will and will not admit, and
every refusal below degrades to ordinary sequential dispatch, which is always
correct and merely slower.

Pure: a tmp directory with a `tasks.md` in it. No git, no docker, no claude.
"""

from __future__ import annotations

import pytest

from devclaw.goal import fanout


def _repo(tmp_path, tasks_md: str, feature: str = "specs/010-feat"):
    d = tmp_path / "ws"
    (d / feature).mkdir(parents=True)
    (d / feature / "tasks.md").write_text(tasks_md, encoding="utf-8")
    return str(d)


TWO_DISJOINT = """# Tasks

- [x] T001 Setup, already done
- [ ] T002 [P] [US1] Renderer (scope: src/widget/**, tests/test_widget.py)
- [ ] T003 [P] [US1] Store (scope: src/store/**, tests/test_store.py)
- [ ] T004 [US1] Wire them together
"""


def test_two_parallel_tasks_with_disjoint_scopes_become_two_lanes(tmp_path):
    lanes = fanout.plan_lanes_sync(_repo(tmp_path, TWO_DISJOINT))
    assert [ln.key for ln in lanes] == ["T002", "T003"]
    assert [ln.position for ln in lanes] == [0, 1]
    assert lanes[0].scopes == ("src/widget/**", "tests/test_widget.py")
    assert lanes[0].feature_dir == "specs/010-feat"
    # each lane gets its OWN checkout — two agents cannot share a working tree
    assert lanes[0].workspace_dir != lanes[1].workspace_dir
    assert lanes[0].workspace_dir.endswith("ws.lanes/T002")


def test_a_plan_with_no_parallel_markers_produces_no_fanout(tmp_path):
    """The byte-identical requirement, at the decision point."""
    plan = "- [ ] T002 [US1] Renderer (scope: src/widget/**)\n- [ ] T003 [US1] Store (scope: src/store/**)\n"
    assert fanout.plan_lanes_sync(_repo(tmp_path, plan)) == []


def test_a_barrier_task_before_the_group_stops_the_fanout(tmp_path):
    """The plan's next step is sequential; the `[P]` group behind it is not
    ready, whatever its markers say."""
    plan = (
        "- [ ] T001 [US1] Migrate the schema first\n"
        "- [ ] T002 [P] [US1] Renderer (scope: src/widget/**)\n"
        "- [ ] T003 [P] [US1] Store (scope: src/store/**)\n"
    )
    assert fanout.plan_lanes_sync(_repo(tmp_path, plan)) == []


def test_a_parallel_task_without_a_declared_scope_blocks_the_whole_fanout(tmp_path):
    """FR-101 admits `[P]` **with** declared scopes. A member with no declared
    I/O is unbounded, so the group is malformed — refuse it entirely rather than
    quietly running its better-behaved siblings."""
    plan = (
        "- [ ] T002 [P] [US1] Renderer (scope: src/widget/**)\n"
        "- [ ] T003 [P] [US1] Store\n"
    )
    assert fanout.plan_lanes_sync(_repo(tmp_path, plan)) == []


def test_overlapping_declared_scopes_are_refused(tmp_path):
    """Hermeticity is decided BEFORE dispatch, not discovered at merge time."""
    plan = (
        "- [ ] T002 [P] [US1] Renderer (scope: src/**)\n"
        "- [ ] T003 [P] [US1] Store (scope: src/store/**)\n"
    )
    assert fanout.plan_lanes_sync(_repo(tmp_path, plan)) == []


def test_a_lone_parallel_task_is_not_a_fanout(tmp_path):
    plan = "- [ ] T002 [P] [US1] Renderer (scope: src/widget/**)\n- [ ] T003 [US1] Wire\n"
    assert fanout.plan_lanes_sync(_repo(tmp_path, plan)) == []


def test_already_checked_tasks_are_never_dispatched_again(tmp_path):
    plan = (
        "- [x] T002 [P] [US1] Renderer (scope: src/widget/**)\n"
        "- [x] T003 [P] [US1] Store (scope: src/store/**)\n"
        "- [ ] T004 [P] [US1] Api (scope: src/api/**)\n"
        "- [ ] T005 [P] [US1] Cli (scope: src/cli/**)\n"
    )
    assert [ln.key for ln in fanout.plan_lanes_sync(_repo(tmp_path, plan))] == ["T004", "T005"]


def test_fanout_never_exceeds_the_host_concurrency_cap(tmp_path, monkeypatch):
    """FR-105: the degree is min(what the plan declared, what the host allows) —
    nothing inside a sandbox contributes to it."""
    plan = "".join(
        f"- [ ] T00{i} [P] [US1] Part {i} (scope: src/p{i}/**)\n" for i in range(2, 8)
    )
    ws = _repo(tmp_path, plan)
    assert len(fanout.plan_lanes_sync(ws, cap=2)) == 2
    assert len(fanout.plan_lanes_sync(ws, cap=4)) == 4
    monkeypatch.setattr("devclaw.task_queue.MAX_CONCURRENT_PER_PROGRAM", 3)
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 8)
    assert fanout.host_cap() == 3
    assert len(fanout.plan_lanes_sync(ws)) == 3


def test_a_cap_of_one_means_no_fanout_at_all(tmp_path):
    """A host that can only run one increment runs one increment — the plan's
    declaration never overrides the host's capacity."""
    assert fanout.plan_lanes_sync(_repo(tmp_path, TWO_DISJOINT), cap=1) == []


def test_a_repo_without_a_speckit_contract_never_fans_out(tmp_path):
    d = tmp_path / "bare"
    d.mkdir()
    assert fanout.plan_lanes_sync(str(d)) == []
    assert fanout.plan_lanes_sync(str(tmp_path / "does-not-exist")) == []


def test_fanout_is_off_unless_the_operator_opts_in(monkeypatch):
    """The dial is real and exercised in both positions: US3 is the spec's
    'earned exception', so an instance adopts it deliberately."""
    monkeypatch.delenv(fanout.FANOUT_ENV, raising=False)
    assert fanout.enabled() is False
    for off in ("", "0", "false", "no", "off", "  OFF "):
        monkeypatch.setenv(fanout.FANOUT_ENV, off)
        assert fanout.enabled() is False, off
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv(fanout.FANOUT_ENV, on)
        assert fanout.enabled() is True, on


# ---- the lane brief --------------------------------------------------------


def test_the_lane_brief_pins_the_task_its_scope_and_the_allocated_spec_directory(tmp_path):
    """FR-104's allocation half: the feature directory the plan already
    allocated is named, and inventing another is forbidden."""
    lanes = fanout.plan_lanes_sync(_repo(tmp_path, TWO_DISJOINT))
    brief = fanout.lane_brief(lanes[0], "Ship the widget", lanes)
    assert "Ship the widget" in brief
    assert "T002" in brief and "Renderer" in brief
    assert "src/widget/**" in brief and "tests/test_widget.py" in brief
    assert "src/store/**" not in brief  # a lane is told its OWN scope
    assert "specs/010-feat" in brief
    assert "Do not create a new `specs/NNN-...` directory." in brief
    assert "T003" in brief  # it knows a sibling is running concurrently


def test_the_lane_brief_forbids_spawning_further_agents(tmp_path):
    """FR-105 in the instruction that actually reaches the worker. The
    enforcement is that the sandbox has no devclaw surface at all (pinned in
    test_scope_gate.py); this is the honest worker's copy of the same rule."""
    lanes = fanout.plan_lanes_sync(_repo(tmp_path, TWO_DISJOINT))
    brief = fanout.lane_brief(lanes[0], "Ship it", lanes)
    assert "Do not start further agents, sub-agents, or background workers." in brief
    assert "decided before you were launched" in brief


def test_fanout_planning_costs_zero_cognition(tmp_path, monkeypatch):
    """Pure fs + string work: no LLM call primitive is reachable from here."""
    import devclaw.llm_call as llm_call

    def _boom(*a, **k):  # pragma: no cover — the point is that it never runs
        raise AssertionError("fan-out planning must never spend a token")

    monkeypatch.setattr(llm_call, "call_claude", _boom, raising=False)
    lanes = fanout.plan_lanes_sync(_repo(tmp_path, TWO_DISJOINT))
    fanout.lane_brief(lanes[0], "obj", lanes)
    assert len(lanes) == 2


@pytest.mark.parametrize("junk", ["", "not a plan", "- [ ] no task id here"])
def test_a_malformed_plan_degrades_to_sequential_dispatch(tmp_path, junk):
    assert fanout.plan_lanes_sync(_repo(tmp_path, junk)) == []
