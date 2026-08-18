"""The queue's program-DAG vocabulary — pure mechanism, no cognition.

:class:`PlannedTask` and :func:`order_tasks` moved here from ``planner.py``
when the host-cognition chain was removed (spec 008 shrink, #539): the worker
plans via speckit in-sandbox, so no host code *produces* plans anymore — but
the TaskQueue's program path still *consumes* a pre-planned DAG (tests, and
any caller that hands the queue explicit tasks), and the engine stub types
against it. This module is a LEAF: its only imports are stdlib +
``state_store`` (the ``TaskKind`` alias) + ``llm_call`` (the ``PlannerError``
raised on a malformed DAG).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm_call import PlannerError
from .state_store import TaskKind


@dataclass
class PlannedTask:
    #: stable id used to express deps within this plan only
    key: str
    goal: str
    kind: TaskKind
    #: keys (not UUIDs) of other tasks in this plan that must finish first
    depends_on_keys: list[str] = field(default_factory=list)
    #: the milestone this task serves (None when omitted)
    milestone: str | None = None
    #: True when the task is *generated scaffolding* (``ng new`` / ``dotnet
    #: new`` boilerplate) so the queue skips ONLY the adversarial review gate
    #: for it — the verify/build gate and the test-integrity scan still run
    #: (enforced in task_queue._run_and_settle). Without this thread a
    #: program-path scaffold diff would hit the review gate and fail closed on
    #: generator output.
    scaffold: bool = False


def order_tasks(tasks: list[PlannedTask]) -> list[PlannedTask]:
    """Validate the DAG shape and return tasks in topological order. Raises
    :class:`PlannerError` on duplicate keys, self-deps, dangling refs, or
    cycles — a cycle that reaches the queue deadlocks the DAG (no task ever
    becomes ready), so every producer of ``list[PlannedTask]`` goes through
    this one check."""
    seen: set[str] = set()
    for t in tasks:
        if t.key in seen:
            raise PlannerError(f"Duplicate task key '{t.key}'")
        seen.add(t.key)
    for t in tasks:
        for d in t.depends_on_keys:
            if d == t.key:
                raise PlannerError(f"Task '{t.key}' depends on itself")
            if d not in seen:
                raise PlannerError(f"Task '{t.key}' depends on unknown key '{d}'")

    # Kahn topological sort — also detects cycles.
    by_key = {t.key: t for t in tasks}
    indegree = {t.key: len(t.depends_on_keys) for t in tasks}
    dependents: dict[str, list[str]] = {}
    for t in tasks:
        for d in t.depends_on_keys:
            dependents.setdefault(d, []).append(t.key)

    ready = sorted(k for k, n in indegree.items() if n == 0)
    ordered: list[PlannedTask] = []
    while ready:
        k = ready.pop(0)
        ordered.append(by_key[k])
        for d in dependents.get(k, []):
            indegree[d] -= 1
            if indegree[d] == 0:
                ready.append(d)
        ready.sort()  # deterministic order across runs

    if len(ordered) != len(tasks):
        raise PlannerError("Plan contains a dependency cycle")
    return ordered


#: Hard BRAKE on one program's task count — a cost backstop, NOT sizing
#: guidance: it stops a runaway plan from enqueueing an unbounded fleet of
#: sandboxed agent runs, never to squeeze a legitimate plan.
MAX_PROGRAM_TASKS = 50
