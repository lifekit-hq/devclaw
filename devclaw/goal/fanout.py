"""Planned fan-out — reading concurrency out of the task graph.

Spec 010 US3 (FR-101/FR-104/FR-105). The executor never *decides* to parallelise
anything. It reads a decision the plan already made: consecutive `[P]` rows with
declared file scopes are tasks the planner asserted are topologically
independent, and this module turns that assertion into a set of lanes to
dispatch. Parallelism is data in the plan, never executor control flow — the
2026-08-18 ruling, adopted from build-system doctrine.

Everything here is a glob, a file read and a string parse. Zero LLM, and it runs
only on the dispatch path (after the phase gates), so no idle or blocked tick
gains any work.

**Off by default.** ``DEVCLAW_FANOUT`` must be set for a goal to fan out at all.
The spec calls US3 the earned exception — "built only after the single-writer
default has run in production" — so the machinery ships complete and the
operator turns it on for a chosen night rather than inheriting it on a redeploy.
Two lanes are also two sandboxes against one OAuth account, which is a spend
decision a person should make on purpose. With the dial off, and with it on for
a plan carrying no `[P]` scopes, dispatch is byte-identical to today.

The admission rules are deliberately strict, and every one of them fails toward
"run sequentially" — the outcome that is always correct, merely slower:

* the next unchecked task must itself be `[P]` (otherwise the plan says the next
  step is a barrier);
* every task in that consecutive `[P]` run must declare a scope — a `[P]` row
  with no declared I/O is an unbounded task, and FR-101 admits only declared
  ones, so ONE undeclared member refuses the whole group rather than quietly
  running its better-behaved siblings;
* the scopes must be pairwise disjoint (:func:`~devclaw.loom.declared_scope.scopes_disjoint`,
  itself conservative);
* at least two lanes must survive the host's concurrency cap.

FR-105 lives here too, in the only place it can: the degree is
``min(what the plan declared, what the host allows)``. Nothing inside a sandbox
contributes to it — the worker is handed one task and told so, and the sandbox
carries no devclaw surface it could ask for another worker from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..loom.declared_scope import parse_plan_rows, scopes_disjoint
from . import slice_guard as _slice_guard

#: The operator dial. Unset/0/false ⇒ no goal ever fans out.
FANOUT_ENV = "DEVCLAW_FANOUT"

#: Suffix of the sibling directory lane workspaces live under. Outside the goal
#: workspace on purpose: a lane checkout inside it would show up in the goal's
#: own git status and in every diff the gates read.
LANES_DIR_SUFFIX = ".lanes"


def enabled() -> bool:
    """Whether planned fan-out is switched on for this instance.

    The var name is spelled out literally rather than read through
    :data:`FANOUT_ENV`: ``tests/test_env_vars_doc_sync.py`` greps the runtime for
    string literals, and a constant-indirected read is invisible to it — which is
    how a dial ends up undocumented."""
    return (os.environ.get("DEVCLAW_FANOUT", "") or "").strip().lower() not in (
        "", "0", "false", "no", "off",
    )


def host_cap() -> int:
    """The most lanes the host will run at once — the queue's own caps, read
    from the one place they are defined so this can never drift above what the
    executor would actually launch (FR-105)."""
    from ..task_queue import GLOBAL_MAX_CONCURRENT, MAX_CONCURRENT_PER_PROGRAM

    return max(1, min(MAX_CONCURRENT_PER_PROGRAM, GLOBAL_MAX_CONCURRENT))


@dataclass(frozen=True)
class Lane:
    """One increment of a fan-out group: a pinned task, its declared I/O, and the
    workspace it runs in."""

    #: the plan's task id (``T012``) — stable, and what the lane brief pins
    key: str
    #: index in the plan; the merge queue integrates in exactly this order
    position: int
    #: the task row's text, as written by the planner
    label: str
    #: the declared file scope — the contract the settle-time gate enforces
    scopes: "tuple[str, ...]"
    #: this lane's own checkout (two agents cannot share one working tree)
    workspace_dir: str
    #: the speckit feature directory this plan lives in, already allocated
    feature_dir: str


def lanes_root(goal_workspace_dir: str) -> str:
    """The sibling directory a goal's lane workspaces live under."""
    return goal_workspace_dir.rstrip("/") + LANES_DIR_SUFFIX


def lane_workspace(goal_workspace_dir: str, key: str) -> str:
    """Where lane ``key`` of this goal checks out."""
    return os.path.join(lanes_root(goal_workspace_dir), key)


def plan_lanes_sync(workspace_dir: str, *, cap: "int | None" = None) -> "list[Lane]":
    """The fan-out group the plan declares next, or ``[]`` for ordinary dispatch.

    Reads the WORKING TREE (the goal branch is checked out) of the feature the
    goal is currently executing. Best-effort and total: any hiccup — no speckit
    contract, an unreadable file, a malformed group — returns ``[]``, which means
    "dispatch one increment as usual". Never raises, and never spends a token."""
    try:
        feature_dir = _slice_guard.current_feature_dir_sync(workspace_dir)
        if not feature_dir:
            return []
        tasks_path = os.path.join(workspace_dir, feature_dir, "tasks.md")
        with open(tasks_path, "r", encoding="utf-8", errors="replace") as fh:
            rows = parse_plan_rows(fh.read())
    except OSError:
        return []
    pending = [r for r in rows if not r.checked]
    if not pending or not pending[0].parallel:
        return []  # the plan's next step is a barrier, not a fan-out group
    run = []
    for row in pending:
        if not row.parallel:
            break
        run.append(row)
    if len(run) < 2:
        return []
    if any(not row.scopes for row in run):
        return []  # an undeclared member refuses the whole group (FR-101)
    limit = host_cap() if cap is None else max(1, cap)
    run = run[:limit]
    if len(run) < 2:
        return []
    for i in range(len(run)):
        for j in range(i + 1, len(run)):
            if not scopes_disjoint(run[i].scopes, run[j].scopes):
                return []
    return [
        Lane(
            key=row.task_id,
            position=idx,
            label=row.label,
            scopes=row.scopes,
            workspace_dir=lane_workspace(workspace_dir, row.task_id),
            feature_dir=feature_dir,
        )
        for idx, row in enumerate(run)
    ]


def lane_brief(lane: Lane, objective: str, siblings: "list[Lane]") -> str:
    """The instruction one lane's worker receives.

    It pins three things the host has already decided and the worker must not
    re-decide: WHICH task (one, named), WHERE it may write (its declared scope,
    enforced mechanically at settle whatever this text says), and that the
    concurrency of this plan is settled — no further agents (FR-105). It also
    names the feature directory the task graph already allocated, so no spec
    directory is ever claimed at runtime (FR-104).

    The scope clause is written as fact, not exhortation, because the gate is
    what enforces it: this text exists so an honest worker knows the boundary,
    not so a dishonest one is deterred."""
    scope_lines = "\n".join(f"  - {g}" for g in lane.scopes)
    others = ", ".join(s.key for s in siblings if s.key != lane.key) or "none"
    return (
        f"{objective}\n\n"
        f"THIS INCREMENT IS ONE LANE OF A PLANNED PARALLEL GROUP.\n\n"
        f"Execute exactly one task from `{lane.feature_dir}/tasks.md`:\n"
        f"  {lane.label}\n\n"
        f"Declared file scope — you may create, edit, move or delete files only "
        f"under:\n{scope_lines}\n"
        f"  - {lane.feature_dir}/tasks.md (to check your own task row off)\n\n"
        f"Lane(s) {others} are running against this repository at the same time, "
        f"each inside its own declared scope. A change outside yours is verified "
        f"and rejected at settle, and this increment fails. If the task cannot be "
        f"done inside its declared scope, stop and say so rather than widening it.\n\n"
        f"The feature directory `{lane.feature_dir}` is already allocated by the "
        f"plan. Do not create a new `specs/NNN-...` directory.\n\n"
        f"Do not start further agents, sub-agents, or background workers. How much "
        f"of this plan runs at once was decided before you were launched.\n\n"
        f"When the task is complete, check its row off in "
        f"`{lane.feature_dir}/tasks.md` and commit your work."
    )
