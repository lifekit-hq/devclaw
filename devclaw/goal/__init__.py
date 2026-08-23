"""The durable goal layer — the standing-intent altitude above the task queue.

A ``program`` is bounded; a ``goal`` is open-ended and driven across many
heartbeats: each tick reads what shipped, plans the next action, dispatches it
into the task queue, and (periodically) evaluates whether the delivered work
is achieving the objective. Modules:

  - ``models``    — Goal / Action / EvalResult / InFlight dataclasses
  - ``store``     — durable on-disk storage (goal.yaml + STATUS.md + log.md + …)
  - ``evaluator`` — judges direction vs ``done_when``, writes corrections back
  - ``summary``   — plain-prose summary of an action for ``deliveries.md``
  - ``mergeability`` — read-only probe: has a delivered PR gone CONFLICTING?
  - ``notify``    — notify hooks (HTTP webhook + null fallback)
  - ``engine``    — dispatches actions into the task queue in-process
  - ``tick``      — the heartbeat loop (the chef's clock)
  - ``service``   — the ``GoalService`` facade the server wires up
"""
