"""Program-plan mechanism tests — llm_call's extract_json and program_plan's
order_tasks (topo/cycles/refs), the pure DAG vocabulary the queue consumes.
The host planning chain (plan_program / planned_from_checklist) was removed
(spec 008 shrink, #539): the worker plans via speckit in-sandbox, and the
queue's default planner refuses un-planned program submissions loudly."""

import json

import pytest

from devclaw.llm_call import PlannerError, extract_json
from devclaw.program_plan import PlannedTask, order_tasks


# ---- extract_json ----


def test_extract_json_leading_whitespace():
    assert extract_json('   {"a":1}  ') == '{"a":1}'


def test_extract_json_fenced():
    assert json.loads(extract_json('```json\n{"tasks":[]}\n```')) == {"tasks": []}


def test_extract_json_prose_preface_and_suffix():
    out = extract_json('Sure! Here:\n{"tasks":[{"key":"t1"}]}\nDone.')
    assert json.loads(out) == {"tasks": [{"key": "t1"}]}


def test_extract_json_no_json_raises():
    with pytest.raises(PlannerError):
        extract_json("no json here")


# ---- order_tasks (DAG shape validation, extracted from the old validate_plan) ----


def _t(key, deps=(), **kw):
    return PlannedTask(key=key, goal="g", kind="implement_feature",
                       depends_on_keys=list(deps), **kw)


def test_single_task_no_deps():
    out = order_tasks([_t("t1")])
    assert len(out) == 1 and out[0].key == "t1"


def test_linear_chain_orders_topologically():
    out = order_tasks([_t("c", ["b"]), _t("b", ["a"]), _t("a")])
    assert [t.key for t in out] == ["a", "b", "c"]


def test_diamond_dag_ordered():
    out = order_tasks([_t("d", ["b", "c"]), _t("b", ["a"]), _t("c", ["a"]), _t("a")])
    order = [t.key for t in out]
    assert order[0] == "a" and order[-1] == "d"
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_cycle_rejected():
    with pytest.raises(PlannerError, match="cycle"):
        order_tasks([_t("a", ["b"]), _t("b", ["a"])])


def test_self_dep_rejected():
    with pytest.raises(PlannerError, match="depends on itself"):
        order_tasks([_t("a", ["a"])])


def test_dangling_ref_rejected():
    with pytest.raises(PlannerError, match="unknown key"):
        order_tasks([_t("a", ["ghost"])])


def test_duplicate_key_rejected():
    with pytest.raises(PlannerError, match="Duplicate"):
        order_tasks([_t("a"), _t("a")])


# ---- the queue's default planner refuses loudly (spec 008 shrink, #539) ----


async def test_default_queue_planner_refuses_loudly(tmp_path):
    """submit_program WITHOUT an injected planner must settle the program
    failed with an actionable reason — host program planning was removed, so
    an un-planned submission is refused loudly, never silently hung."""
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    store = StateStore(str(tmp_path / "t.db"))
    try:
        async def runner(req):  # pragma: no cover - planning fails first
            raise AssertionError("no task may launch without a plan")

        q = TaskQueue(store, runner=runner)
        program_id = q.submit_program(workspace_dir="/ws", goal="big goal")
        await q.drain()
        p = store.get_program(program_id)
        assert p.status == "failed"
        assert "host program planning was removed" in (p.error or "")
    finally:
        store.close()


def test_oversized_plan_trips_the_program_task_brake():
    """The MAX_PROGRAM_TASKS cost brake survives the shrink at the order_tasks
    choke point: an oversized DAG is rejected before any row could be written
    (reborn from the deleted checklist-adapter brake test — spec 008 shrink)."""
    from devclaw.program_plan import MAX_PROGRAM_TASKS, PlannedTask, order_tasks

    tasks = [
        PlannedTask(key=f"t{i}", goal=f"task {i}", kind="implement_feature")
        for i in range(MAX_PROGRAM_TASKS + 1)
    ]
    with pytest.raises(PlannerError, match="program brake"):
        order_tasks(tasks)
    # At the cap exactly: allowed.
    assert len(order_tasks(tasks[:MAX_PROGRAM_TASKS])) == MAX_PROGRAM_TASKS
