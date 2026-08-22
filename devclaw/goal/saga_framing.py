"""The saga framing section of the advance brief — five named slots, not prose
(spec 012 US2).

A saga's framing is re-sent IN FULL with every unit of work (FR-009a): a fresh
sandbox has no memory, so a pointer would be a request while a slot is a fact.
That multiplies the framing's cost by the increment count, which is why this
module exists at all — it replaces two unbounded prose strings with a fixed
structure whose size is bounded by construction (FR-009b).

The schema (FR-007) and the reader that ACTS on each slot (FR-009 — a slot that
changes nothing a worker does must not exist):

============== ===================== ==========================================
what           field                 who acts on it
============== ===================== ==========================================
achieved       ``objective``         the worker's one statement of what to pursue
completion     ``done_when``         the done-gate's per-clause contract
excluded       ``out_of_scope``      the worker: work it must not build into
invariants     ``invariants``        the worker: what must still hold after it
established    ``established``       the worker: decisions it must not re-derive
============== ===================== ==========================================

``established`` is the STATIC sibling of US1's feed-forward: that section says
what previous increments SHIPPED, this one says what was decided before any
increment ran.

Two rules shape the rendering:

* **A declared-empty slot states its absence; it is never omitted.** Same
  doctrine as FR-004 — an author who excluded nothing and an author who forgot
  must not produce the same prompt. This is what makes two independently
  authored sagas structurally identical (SC-003).
* **A slot that is ``None`` is omitted entirely.** ``None`` means the goal was
  authored BEFORE this schema, so its framing renders exactly as it did then —
  live prose-authored goals keep working unchanged.

Pure and never-raises: framing composition sits on the dispatch path, and a bad
slot value must degrade, never wedge a dispatch (constitution VI).
"""

from __future__ import annotations

from ..advance_brief import GOAL_LINE_PREFIX
from .prompt_budget import cap_saga_slot

#: Section labels. One home, so a test and the generator cannot drift.
DONE_WHEN_PREFIX = "Done when:"
OUT_OF_SCOPE_LABEL = "Out of scope"
INVARIANTS_LABEL = "Invariants"
ESTABLISHED_LABEL = "Already established"

#: ``(label, imperative-when-filled, statement-when-declared-empty)``, in render
#: order. Each imperative is stated ONCE (rules/cognition-prompts.md).
_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "out_of_scope",
        OUT_OF_SCOPE_LABEL,
        " — do NOT build or change these, even where they look adjacent:",
        ": nothing is excluded — everything the goal requires is in scope.",
    ),
    (
        "invariants",
        INVARIANTS_LABEL,
        " — these must still hold after this increment; a change that breaks "
        "one is not shippable:",
        ": none declared beyond the repository's own documented contracts.",
    ),
    (
        "established",
        ESTABLISHED_LABEL,
        " — settled decisions: build on them, do NOT re-derive or re-litigate "
        "them:",
        ": nothing recorded — establish what you need and record it in the repo.",
    ),
)


def _items(value: object) -> list[str]:
    """The non-blank entries of a slot, defensively — a slot is authored data
    and this runs on the dispatch path."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _section(label: str, imperative: str, empty_statement: str, value: object) -> str:
    items = _items(value)
    if not items:
        return label + empty_statement
    body = cap_saga_slot("\n".join(f"- {i}" for i in items))
    return label + imperative + "\n" + body


def render(goal) -> str:
    """The saga framing block of the advance brief, for ``goal``.

    Always opens with the ``Goal:`` line every detector keys off
    (:data:`devclaw.advance_brief.GOAL_LINE_PREFIX`). The three US2 slots render
    only when the goal carries them — a goal whose ``goal.yaml`` predates the
    schema (slots ``None``) produces the pre-US2 framing byte-for-byte, save for
    a slot that exceeds the FR-009b budget."""
    try:
        blocks = [f"{GOAL_LINE_PREFIX} {cap_saga_slot(goal.objective)}"]
        done_when = (goal.done_when or "").strip()
        if done_when:
            blocks.append(f"{DONE_WHEN_PREFIX} {cap_saga_slot(done_when)}")
        for attr, label, imperative, empty_statement in _SECTIONS:
            value = getattr(goal, attr, None)
            if value is None:
                continue  # authored before the schema — omit, don't invent
            blocks.append(_section(label, imperative, empty_statement, value))
        return "\n\n".join(blocks)
    except Exception:  # noqa: BLE001 — never wedge a dispatch over framing
        return f"{GOAL_LINE_PREFIX} {getattr(goal, 'objective', '')}"
