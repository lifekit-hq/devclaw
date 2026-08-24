"""The queue package — TaskQueue's mixin modules.

:class:`devclaw.task_queue.TaskQueue` stays the class's one home (and the
import path every caller uses); these modules carry its split-out method
groups, same idiom as ``state_store/`` and ``goal/store/``:

- :mod:`.settle` — the execute/settle path (``_execute`` / ``_run_and_settle``
  / branch prep / reachability valve) plus the seams they resolve as globals.
- :mod:`.programs` — the DAG-program lifecycle (submit/plan/schedule/
  terminalize/cancel).
- :mod:`.admission` — the launch brakes (host-memory budget, workspace
  circuit-breaker).

Dependency direction is one-way: ``task_queue`` imports from here; nothing
here imports ``devclaw.task_queue`` at runtime.
"""
