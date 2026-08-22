"""Gate strictness policy (ADR 0007) — the ONE pure function that turns a gate
*failure* plus the goal's strictness dial into a consequence: **block** the goal
(fail closed, today's behavior) or **advise** (record loud + surface in the PR
body, ship anyway; the human merge is the backstop).

Pure module — no I/O, no subprocess — the same shape as :mod:`browser_gate`. The
entire strict/trust policy is legible here in one screen, which is the point:
this change recalibrates the "loud failure over silent degradation" invariant,
so the whole consequence table must be reviewable at a glance.

Design (ADR 0007): the dial is *data* (a ``Strictness`` value on the goal) and
the consequence is *this one function*, NOT a Strategy-pattern object per mode —
strict vs. trust is a one-branch difference and there is no third variant. The
Strategy-shaped seam is reserved for the deferred judge-gate (how a verdict is
*formed*), a different knob entirely.

Note on the signature: this maps an *already-decided failure* to a consequence,
so it takes ``(gate_id, strictness)`` and not each gate's verdict vocabulary —
"did this gate fail?" stays inside each gate's own verdict logic (browser:
``ran_failed``/``never_ran``/``absent``; review: its own), keeping the policy
gate-agnostic. Callers invoke it only when a gate has produced a blocking-
candidate failure.
"""

from __future__ import annotations

from enum import Enum

#: Gate ids that IGNORE the strictness dial and always fail closed, in BOTH
#: trust and strict modes. They guard against the model gaming its own evidence
#: or closing a goal on its own say-so — failure modes the human merge on
#: open-PR-only delivery does NOT reliably catch, so the dial must never loosen
#: them (ADR 0007 decision 3). The two dial-able gates (browser-E2E, adversarial
#: review) are exactly the ones NOT in this set. NOTE: in practice only the two
#: dial-able gates are routed through :func:`gate_consequence` at the settle
#: cascade; this set is the belt-and-suspenders guarantee that an always-hard
#: gate, if ever routed here by mistake, still blocks.
ALWAYS_HARD: frozenset[str] = frozenset(
    {
        "verify",  # the verify_cmd gate — green tests are table stakes
        "materialize",  # the agent's change could not be determined at all
        "test_integrity",  # tests silently deleted/neutered
        "scope",  # a [P] increment writing outside its declared file scope
        "delivery_trust",  # red CI merged (remote_checks / CI gate)
        "done_gate",  # closing a goal on the model's own say-so
    }
)
# ``materialize`` (spec 013 FR-007) is hard in BOTH modes because it is the
# precondition of every other read-the-change gate: if the span cannot be
# determined, no gate below it has an input, and an empty span is no longer
# safely equivalent to "nothing changed". Shipping on it would be shipping on
# silence — the #186 case exactly.

# ``scope`` (spec 010 FR-103) is hard in BOTH modes on purpose: a declared file
# scope is what makes two increments safe to run at once, so an increment that
# writes outside it has invalidated the premise of its own concurrency, not
# merely produced a finding a human could weigh at the merge boundary. It is
# also the #358 class — a worker routing around a constraint — which is exactly
# what the dial must never loosen. FR-103 states fail-closed with no mode
# qualifier.

#: The dial-able gates, named for clarity and tests. NOT load-bearing —
#: :func:`gate_consequence` keys off ``ALWAYS_HARD`` membership, so a gate is
#: hard only by appearing in ``ALWAYS_HARD``; a new dial-able gate needs nothing
#: added here to be advisory under ``trust``. ``slice`` is the milestone-keyed
#: mega-dump guardrail (SDLC pipeline P2, resolved in ``goal.tick_settle``):
#: under ``trust`` a ``>1``-milestone increment advises-and-ships, under
#: ``strict`` it blocks for a re-slice.
DIAL_ABLE: frozenset[str] = frozenset({"browser", "review", "slice"})


class Consequence(Enum):
    """What a gate failure does under the goal's strictness dial."""

    #: fail closed — the failure blocks the task (today's behavior)
    BLOCK = "block"
    #: ship anyway — record the failure loud + surface it in the PR body
    ADVISE = "advise"


def gate_consequence(gate_id: str, strictness: str) -> Consequence:
    """Map a gate *failure* to its consequence under ``strictness``.

    Called only when gate ``gate_id`` has already produced a blocking-candidate
    failure (a pass has no decision to make).

    - An **always-hard** gate (``gate_id in ALWAYS_HARD``) blocks regardless of
      the dial.
    - A **dial-able** gate blocks under ``"strict"`` and advises under
      ``"trust"``.
    - An **unrecognized** ``strictness`` value is treated as ``"strict"`` (fail
      closed) — the safe default: an unknown dial must never silently loosen a
      gate. This mirrors the loud-failure invariant this policy recalibrates.
    """
    if gate_id in ALWAYS_HARD:
        return Consequence.BLOCK
    if strictness == "trust":
        return Consequence.ADVISE
    return Consequence.BLOCK
