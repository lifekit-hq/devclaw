"""The queue package — TaskQueue's mixin modules.

- :mod:`.settle` — the execute/settle path: place the branch, run ONE session,
  classify how it ended, materialize the span, run the gates, deliver.
- :mod:`.admission` — the launch brake (the per-workspace failure breaker).

Dependency direction is one-way: ``task_queue`` imports from here; nothing
here imports ``devclaw.task_queue`` at runtime.
"""
