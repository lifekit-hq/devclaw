"""The saga framing — five named slots, not prose (spec 012 US2).

Structure, the explicitly-empty case, the pre-schema case (live goals authored
before this schema must render byte-identically), and the FR-009b size bound.
Every prompt-content assertion pins BOTH presence and absence: a slot that
renders when it should be omitted is as wrong as one that goes missing.
"""

from __future__ import annotations

from devclaw.advance_brief import GOAL_LINE_PREFIX
from devclaw.goal import saga_framing as sf
from devclaw.goal.models import Goal
from devclaw.goal.prompt_budget import (
    SAGA_FRAMING_MAX,
    SAGA_SLOT_KEEP,
    SAGA_SLOT_TRUNCATION_MARKER,
)


def _goal(**kw) -> Goal:
    base = dict(
        id="g",
        objective="ship the health endpoint",
        cadence="1d",
        engine="devclaw",
        workspace_dir="/ws",
        done_when="GET /health returns HTTP 200 with status:ok in the body.",
    )
    base.update(kw)
    return Goal(**base)


#: The pre-US2 framing, spelled out literally rather than imported from the
#: generator — a regression test that derives its expectation from the code it
#: guards cannot catch the code changing.
_PRE_US2 = (
    "Goal: ship the health endpoint\n\n"
    "Done when: GET /health returns HTTP 200 with status:ok in the body."
)


def test_saga_framing_renders_every_slot_in_a_fixed_order_with_its_imperative():
    """FR-007: all five slots, in one fixed order, each carrying the imperative
    that makes the worker act on it."""
    text = sf.render(_goal(
        out_of_scope=["the mobile client", "authentication"],
        invariants=["the existing test suite stays green"],
        established=["Postgres is the datastore — decided, do not revisit"],
    ))

    # The five slots, in order.
    positions = [
        text.index(GOAL_LINE_PREFIX),
        text.index(sf.DONE_WHEN_PREFIX),
        text.index(sf.OUT_OF_SCOPE_LABEL),
        text.index(sf.INVARIANTS_LABEL),
        text.index(sf.ESTABLISHED_LABEL),
    ]
    assert positions == sorted(positions)

    # Content, verbatim.
    assert "- the mobile client" in text
    assert "- authentication" in text
    assert "- the existing test suite stays green" in text
    assert "- Postgres is the datastore — decided, do not revisit" in text

    # Each slot carries the instruction that makes it act on the worker
    # (FR-009): a label with no imperative is decoration.
    assert "do NOT build or change these" in text
    assert "not shippable" in text
    assert "do NOT re-derive or re-litigate" in text


def test_explicitly_empty_slot_states_its_absence_instead_of_being_omitted():
    """An author who excluded nothing and an author who forgot must not produce
    the same prompt — same doctrine as FR-004's stated absence."""
    text = sf.render(_goal(out_of_scope=[], invariants=[], established=[]))

    assert "Out of scope: nothing is excluded" in text
    assert "Invariants: none declared" in text
    assert "Already established: nothing recorded" in text
    # Presence AND absence: the filled-slot imperatives must NOT appear when
    # there is nothing to apply them to.
    assert "do NOT build or change these" not in text
    assert "do NOT re-derive or re-litigate" not in text


def test_two_sagas_with_different_content_render_identical_section_structure():
    """SC-003: two people authoring the same objective produce sagas with the
    same structure, differing only in content."""
    a = sf.render(_goal(
        objective="A", done_when="a" * 40,
        out_of_scope=["x"], invariants=["y"], established=["z"],
    ))
    b = sf.render(_goal(
        objective="B", done_when="b" * 40,
        out_of_scope=["p", "q"], invariants=[], established=["r"],
    ))

    def labels(text: str) -> list[str]:
        return [
            line.split("—")[0].split(":")[0].strip()
            for line in text.splitlines()
            if line and not line.startswith("- ")
        ]

    assert labels(a) == labels(b)
    assert labels(a) == [
        "Goal", "Done when", sf.OUT_OF_SCOPE_LABEL,
        sf.INVARIANTS_LABEL, sf.ESTABLISHED_LABEL,
    ]


def test_goal_authored_before_the_slot_schema_renders_todays_framing_byte_identical():
    """Backward compatibility, structurally rather than best-effort: a goal
    whose goal.yaml has no slot keys carries ``None`` (not ``[]``), and its
    framing is the pre-US2 string byte-for-byte. Live prose-authored goals on
    the VPS keep their exact brief."""
    goal = _goal()
    assert goal.out_of_scope is None  # absent key, not an empty declaration
    text = sf.render(goal)

    assert text == _PRE_US2
    for label in (sf.OUT_OF_SCOPE_LABEL, sf.INVARIANTS_LABEL, sf.ESTABLISHED_LABEL):
        assert label not in text


def test_a_partially_migrated_goal_renders_only_the_slots_it_carries():
    """The None-vs-[] distinction is per slot, not per goal: filling one slot
    must not fabricate the other two."""
    text = sf.render(_goal(out_of_scope=["the mobile client"]))

    assert "- the mobile client" in text
    assert sf.INVARIANTS_LABEL not in text
    assert sf.ESTABLISHED_LABEL not in text


def test_oversized_slot_content_is_bounded_and_says_so():
    """FR-009b. HEAD-kept, unlike the history caps: an author states the
    contract first, so the opening is the part that must survive."""
    text = sf.render(_goal(
        out_of_scope=["first exclusion, the one that matters"] + ["x" * 400] * 20,
        invariants=[], established=[],
    ))

    assert "first exclusion, the one that matters" in text
    assert "truncated to fit the prompt budget" in text


def test_whole_saga_framing_stays_under_the_declared_bound_for_adversarial_input():
    """The bound FR-009b actually asks for is on the FRAMING, not on any one
    slot — because the framing is re-sent with every increment (FR-009a), so
    its size is multiplied by the increment count."""
    blob = "y" * 50_000
    text = sf.render(_goal(
        objective=blob, done_when=blob,
        out_of_scope=[blob], invariants=[blob], established=[blob],
    ))

    assert len(text) <= SAGA_FRAMING_MAX
    # Each of the five slots is individually bounded, so no single slot can
    # crowd out the others — five truncations, no surviving over-budget run.
    assert text.count(SAGA_SLOT_TRUNCATION_MARKER) == 5
    assert "y" * (SAGA_SLOT_KEEP + 1) not in text


def test_saga_framing_never_raises_on_a_malformed_slot():
    """Composition sits on the dispatch path: a bad slot value degrades, it
    never wedges a dispatch (constitution VI)."""
    text = sf.render(_goal(out_of_scope="not a list", invariants=[None], established=[]))

    assert text.startswith(GOAL_LINE_PREFIX)
    assert "Out of scope: nothing is excluded" in text
