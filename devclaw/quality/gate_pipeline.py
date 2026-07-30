"""Gate pipeline — the decision-only spine of the settle cascade (#407).

The queue's ``_run_and_settle`` used to carry the post-verify quality checks as
an inline short-circuit ladder: compute the diff once, run test-integrity, then
(only if that passed) the adversarial review, then (only if BOTH passed) the
browser-E2E gate, threading a hand-rolled ``elif`` chain. This module lifts that
ladder into an explicit, uniform **pipeline of pure verdict-producer gates** so
the ordering and short-circuit are legible in one place — WITHOUT moving any
state mutation out of the single-writer :class:`~devclaw.task_queue.TaskQueue`.

The split of responsibilities is deliberate and load-bearing:

* A **gate** (`Gate` protocol) is a *pure verdict producer*. In: read-only run
  artifacts (the runner ``verify`` dict, the shared diff, the workspace path).
  Out: a :class:`GateVerdict` — ``ok`` or a failure ``reason``. A gate NEVER
  mutates a task row, writes goal/queue state, or calls ``mark_*`` / ``requeue``
  / ``set_global_pause``. (It may read the workspace and, for the two cognition
  gates, call ``claude`` — those reads/calls already lived in the wrapped
  functions and are unchanged; the invariant this module adds is *no writes*.)
* :func:`run_pipeline` iterates an ORDERED tuple of gates, honours each gate's
  ``applies()`` predicate, and **short-circuits on the first non-ok verdict**.
  That short-circuit IS the strict ordering the cascade always had: review runs
  only if integrity passed, browser only if both passed — because a non-ok
  integrity verdict stops the iteration before review is ever reached.
* The **ADR-0007 strictness dial stays central** in :mod:`.gate_policy`
  (``gate_consequence`` + the ``ALWAYS_HARD`` frozenset). A gate only flags
  whether its finding is ``dialable``; the orchestrator (the queue) — not the
  gate — decides block-vs-advise. Policy never moves into a gate.
* The **failure-classification / pause / fast-fail axis** (worker-block marker →
  ``classify_failure`` → review-crash marker) is a SEPARATE axis and stays in the
  queue, with its load-bearing ordering intact. This module is only Axis 1 (the
  gate verdict); it does not touch the pause axis.

Single shared diff: the diff is computed at most once per settle and only when
it is actually needed. :class:`GateInput` memoises it behind an async accessor —
the verify gate never asks for it (so a verify failure short-circuits before any
``git diff`` runs, exactly as before), and integrity/review/browser share the
one computed value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class GateInput:
    """Read-only run artifacts handed to every gate in one settle.

    The diff is the load-bearing shared resource: it is computed AT MOST ONCE
    (lazily, via :meth:`diff`) and only when a gate that reads it actually runs.
    The verify gate does not touch it, so a verify failure short-circuits the
    pipeline before any ``git diff`` subprocess fires — preserving the cascade's
    "compute the diff once, and only after verify passed" behaviour byte-for-byte.
    """

    kind: str
    goal: str
    workspace_dir: str
    #: the runner's ``verify`` sub-result (``{"ran": bool, "passed": bool, ...}``)
    verify: Optional[dict]
    #: scaffold task (L3, #222) — the adversarial review gate self-skips on it
    scaffold: bool
    #: browser-gate stance (``flexible`` | ``strict``) resolved for this workspace
    browser_mode: str
    #: async producer of the post-run diff vs. the pre-run base. Called at most
    #: once; the result is cached on :attr:`_diff_value`.
    diff_fn: Callable[[], Awaitable[str]]
    _diff_computed: bool = field(default=False, repr=False)
    _diff_value: str = field(default="", repr=False)

    async def diff(self) -> str:
        """The shared post-run diff, computed once and memoised. An empty diff is
        a valid value, so a boolean flag (not the string's truthiness) guards the
        one-time computation."""
        if not self._diff_computed:
            self._diff_value = await self.diff_fn()
            self._diff_computed = True
        return self._diff_value


@dataclass(frozen=True)
class GateVerdict:
    """One gate's decision. ``ok`` gates let the cascade continue; a non-ok gate
    carries the ``reason`` fed back into the retry loop (and, for a ``dialable``
    gate, remembered by the orchestrator for a possible ADR-0007 advise-and-ship
    at retry exhaustion). ``dialable`` is a FLAG, not a policy: the orchestrator
    still asks :func:`~devclaw.quality.gate_policy.gate_consequence` what to do
    with it."""

    gate_id: str
    ok: bool
    reason: Optional[str] = None
    #: whether this gate's finding is an ADR-0007 dial-able one (browser / review)
    dialable: bool = False

    @classmethod
    def passed(cls, gate_id: str) -> "GateVerdict":
        return cls(gate_id=gate_id, ok=True)

    @classmethod
    def failed(
        cls, gate_id: str, reason: str, *, dialable: bool = False
    ) -> "GateVerdict":
        return cls(gate_id=gate_id, ok=False, reason=reason, dialable=dialable)


@runtime_checkable
class Gate(Protocol):
    """A pure verdict producer. Reads :class:`GateInput`, returns a
    :class:`GateVerdict`. No row mutation, no store writes — the single-writer
    invariant lives in the orchestrator, never here."""

    gate_id: str

    def applies(self, gate_input: GateInput) -> bool:
        """Whether this gate participates for this input at all. A gate that does
        not apply is skipped (treated as a pass) and the pipeline continues to the
        next gate — distinct from a gate that runs and returns ``ok``."""
        ...

    async def check(self, gate_input: GateInput) -> GateVerdict:
        """Produce this gate's verdict. Called only when :meth:`applies` is True."""
        ...


async def run_pipeline(
    gate_input: GateInput, gates: Sequence[Gate]
) -> Optional[GateVerdict]:
    """Run an ORDERED sequence of gates, short-circuiting on the first non-ok
    verdict. Returns that first failing :class:`GateVerdict`, or ``None`` if every
    applicable gate passed.

    The short-circuit is the whole point: because a non-ok verdict stops the
    loop, a later gate runs only when every earlier gate passed — which is exactly
    the cascade's strict ordering (review only if integrity passed, browser only
    if both passed). A gate whose :meth:`~Gate.applies` is False is skipped
    without a verdict and does not stop the chain.
    """
    for gate in gates:
        if not gate.applies(gate_input):
            continue
        verdict = await gate.check(gate_input)
        if not verdict.ok:
            return verdict
    return None
