"""Prompt-size budget helpers shared across the cognition callers.

The #422 class: a prompt section bounded by ROW COUNT but NOT by SIZE grows
unbounded over a goal's life, and a handful of fat rows push the assembled
``claude --print`` prompt past the point where time-to-first-token climbs into
the cognition timeout (default 180 s). The call then fires the timeout having
produced ZERO output (self-filed ``timeout input_chars=… bytes_out=0
first_byte_ms=none``), and it RECURS because the next tick re-sends the same
bloated prompt and re-times-out identically.

#426 capped the planner's ``recent_log`` in place; this module is the shared
home so every caller embedding the same growing sections caps them the SAME way
instead of each re-discovering the fix (the doctrine: fix the class, not the
instance). #431 brought the evaluator on board — its prompt embeds the same
unbounded ``recent_log`` plus a ``deliveries`` tail that likewise grows over a
long goal's life.

Growing-history caps TAIL-KEEP: the most-recent content is what the next
reasoning step turns on, so oversized input keeps its tail behind a truncation
marker. AUTHORED caps (``cap_saga_slot``, spec 012 US2) invert that and
HEAD-keep: an author states the contract first and elaborates after, so the head
is the part that must survive. Either way, small or empty input passes through
BYTE-IDENTICAL, so existing call sites and test stubs are unaffected (and ``""``
stays ``""`` to preserve callers' ``or "(fallback)"``).

Scope note — these cap UNBOUNDED CONTEXT sections only. They are NOT a general
prompt ceiling: you must never blind-truncate an assembled prompt (it would risk
cutting the JSON-output contract at the tail — the very "No JSON object found"
crash class), never truncate a diff under review (you can't shrink the thing you
are reviewing — the review gate's oversized-diff case is deliberately
fail-closed, #186/#382), and never elide fixed instruction text.
"""

from __future__ import annotations


def cap_section(text: str, *, keep: int, marker: str) -> str:
    """Tail-keep ``text`` to at most ``keep`` chars behind ``marker``. Input at
    or under the budget passes through byte-identical (so ``""`` stays ``""``)."""
    if len(text) <= keep:
        return text
    return "\n".join((marker, text[-keep:]))


#: 24 KB ≈ 6k tokens — ample for real recent history, an order of magnitude under
#: the timeout-inducing size, with headroom under the same budget for the other
#: (small / already-bounded) prompt sections.
LOG_KEEP = 24_000

#: Rendered as its own line where content was elided, so the model knows it is
#: reading the TAIL of the log, not the whole history.
LOG_TRUNCATION_MARKER = (
    "[…older log lines elided to fit the prompt budget: most-recent events "
    "kept — the full history is in the goal log / task records]"
)


def cap_log(recent_log: str) -> str:
    """Bound a recent-history (log) section before it rides into a cognition
    prompt. Tail-kept (newest events are what the next action turns on); small or
    empty logs pass through byte-identical."""
    return cap_section(recent_log, keep=LOG_KEEP, marker=LOG_TRUNCATION_MARKER)


#: Same budget as the log. ``deliveries`` (the grounded per-action shipped-summary
#: tail) also grows unbounded over a long goal's life and rode into the evaluator
#: prompt uncapped — the #431 evaluator-timeout half of the class.
DELIVERIES_KEEP = 24_000

DELIVERIES_TRUNCATION_MARKER = (
    "[…older delivery summaries elided to fit the evaluation budget: most-recent "
    "deliveries kept — the full record is in the goal deliveries view]"
)


def cap_deliveries(deliveries: str) -> str:
    """Bound the grounded-deliveries section. Tail-kept (the most-recent
    deliveries are the current state of the shipped work); small or empty passes
    through byte-identical."""
    return cap_section(
        deliveries, keep=DELIVERIES_KEEP, marker=DELIVERIES_TRUNCATION_MARKER
    )


#: The saga feed-forward section of the worker's advance brief (spec 012 US1).
#: Deliberately a QUARTER of the log/deliveries budgets: unlike those — read once
#: per cognition call — this section is re-sent with EVERY increment (FR-009a),
#: so its cost multiplies by the increment count over a saga's life. 6 KB holds
#: tens of compact one-line entries, which is what FR-009b's bound is for.
PRIOR_INCREMENTS_KEEP = 6_000

PRIOR_INCREMENTS_TRUNCATION_MARKER = (
    "[…older increments elided to fit the prompt budget: the most-recent ones "
    "are kept — the full record is in the goal deliveries view]"
)


#: spec 031 US4 — the Decisions feed-forward section rides the same budget
#: discipline as prior increments: re-sent with every dispatch, so bounded.
DECISIONS_KEEP = 4_000

DECISIONS_TRUNCATION_MARKER = (
    "[…older decisions elided to fit the prompt budget: the most-recent ones "
    "are kept; the full ledger is on the goal (get_goal → decisions)]"
)


def cap_decisions(section: str) -> str:
    """Bound the Decisions feed-forward section. Tail-kept (the newest rulings
    are the ones most likely to govern the next increment); small or empty
    passes through byte-identical."""
    return cap_section(
        section, keep=DECISIONS_KEEP, marker=DECISIONS_TRUNCATION_MARKER
    )


def cap_prior_increments(section: str) -> str:
    """Bound the prior-increments feed-forward section. Tail-kept (the newest
    increments are the ones the next session builds on); small or empty passes
    through byte-identical."""
    return cap_section(
        section, keep=PRIOR_INCREMENTS_KEEP, marker=PRIOR_INCREMENTS_TRUNCATION_MARKER
    )


#: One authored saga slot (spec 012 US2). Five slots ride the framing that
#: FR-009a re-sends with EVERY increment, so the per-slot bound is what makes
#: the whole framing bounded by construction (FR-009b) — see SAGA_FRAMING_MAX.
#: 1 200 chars is several paragraphs: generous for a real slot, decisive
#: against a pasted design doc.
SAGA_SLOT_KEEP = 1_200

SAGA_SLOT_TRUNCATION_MARKER = (
    "[…this slot was truncated to fit the prompt budget: its opening is kept — "
    "the full text is in the goal's spec / goal.yaml]"
)


def cap_saga_slot(text: str) -> str:
    """Bound ONE authored saga slot. HEAD-kept, unlike every history cap above:
    an author puts the contract first, so truncating the tail keeps the binding
    part. Small or empty input passes through byte-identical."""
    if len(text) <= SAGA_SLOT_KEEP:
        return text
    return "\n".join((text[:SAGA_SLOT_KEEP], SAGA_SLOT_TRUNCATION_MARKER))


#: The derived ceiling on a whole rendered saga framing: five capped slots plus
#: the fixed labels and truncation markers. Stated as a constant (rather than
#: left implicit) because FR-009b's bound is on the FRAMING, not on any one
#: slot, and a test asserts adversarial input renders under it.
SAGA_FRAMING_MAX = 5 * (SAGA_SLOT_KEEP + len(SAGA_SLOT_TRUNCATION_MARKER)) + 2_000


#: The steering section of the advance brief (spec 025). Unlike log/deliveries
#: (read once per cognition call), this section is re-sent with EVERY dispatch
#: for the life of the goal — its cost multiplies by dispatch count. A later
#: steering line typically corrects an earlier one, so tail-keep is the right
#: policy: the newest line always survives, older ones compact when the budget
#: is exceeded. 4 KB ≈ 20 typical corrections (~200 chars each).
STEERING_KEEP = 4_000

STEERING_TRUNCATION_MARKER = (
    "[…older steering lines compacted to fit the dispatch budget: "
    "the most-recent line is kept — review the goal inbox for the full history]"
)


def cap_steering(text: str) -> str:
    """Bound the steering section of the advance brief. Tail-kept: the newest
    steering line is at the tail and always survives; older lines are compacted
    behind ``STEERING_TRUNCATION_MARKER`` when the budget is exceeded. Small or
    empty input passes through byte-identical."""
    return cap_section(text, keep=STEERING_KEEP, marker=STEERING_TRUNCATION_MARKER)
