"""The saga feed-forward section — spec 012 US1.

An increment must know what its siblings delivered, and must know it from
devclaw's OWN settlement records rather than from the previous worker's
self-report (#358). These tests pin both halves: what the section says, and
what it refuses to carry.
"""

from __future__ import annotations

from devclaw.advance_brief import PRIOR_INCREMENTS_MARKER, display_goal
from devclaw.goal.prior_increments import (
    IncrementRecord, parse_record, render,
)
from devclaw.goal.prompt_budget import (
    PRIOR_INCREMENTS_KEEP, PRIOR_INCREMENTS_TRUNCATION_MARKER,
)

# The real shape engine._task_detail writes into a delivery body.
SHIPPED_BODY = """PR: https://github.com/acme/repo/pull/7

Agent summary:
I refactored the widget module and it is all working great now.

Verify gate `pytest -q`: PASSED
Gate output (tail):
120 passed
"""

FAILED_BODY = """Agent summary:
I tried the migration but could not finish.

Verify gate `pytest -q`: FAILED
Gate output (tail):
3 failed

Error:
sandbox timed out after 3600s
"""


def test_first_increment_brief_states_no_prior_increments_explicitly():
    """FR-004 / acceptance 2: the absence is STATED, never omitted — a worker
    must never have to infer whether it is the first increment."""
    section = render([])

    assert PRIOR_INCREMENTS_MARKER in section
    assert "this is the first" in section.lower()
    assert "do not assume any part of the goal is already built" in section.lower()


def test_second_increment_brief_states_prior_delivery_outcome_and_verdict():
    """FR-002/FR-003 / acceptance 1: position, outcome, verdict, and PR."""
    rec = parse_record("add the /health endpoint", SHIPPED_BODY, "done")
    section = render([rec])

    assert "This is increment 2 of this goal" in section
    assert "add the /health endpoint" in section
    assert "status=done" in section
    assert "gate=PASSED" in section
    assert "PR=https://github.com/acme/repo/pull/7" in section


def test_prior_increment_agent_summary_never_rides_the_feed_forward():
    """#358: the previous worker's own prose is NOT evidence. Only devclaw's
    controlled fields (status, gate verdict, PR, error) cross this channel."""
    rec = parse_record("add the /health endpoint", SHIPPED_BODY, "done")
    section = render([rec])

    assert "all working great" not in section
    assert "Agent summary" not in section
    assert "120 passed" not in section  # raw gate output stays out too


def test_failed_prior_increment_reported_in_next_brief():
    """FR-005 / acceptance 3: a failed increment reports its failure and reason
    so the attempt is not repeated unchanged."""
    rec = parse_record("migrate the store", FAILED_BODY, "failed")
    section = render([rec])

    assert "status=failed" in section
    assert "gate=FAILED" in section
    assert "sandbox timed out after 3600s" in section
    assert "did NOT land" in section


def test_prior_increments_section_is_bounded_and_elides_loudly():
    """FR-009b / SC-006: re-sent every increment, so the entry list is bounded —
    tail-kept (newest survive) behind a marker that names the elision. The
    framing header must survive the cap, or every detector keying off the
    marker breaks."""
    records = [
        IncrementRecord(
            objective=f"increment {i} " + "x" * 300, status="done", gate="PASSED",
        )
        for i in range(60)
    ]

    section = render(records)

    assert len(section) < PRIOR_INCREMENTS_KEEP + 2_000
    assert PRIOR_INCREMENTS_TRUNCATION_MARKER in section
    assert PRIOR_INCREMENTS_MARKER in section          # header survived
    assert "This is increment 61 of this goal" in section
    assert "increment 59" in section                   # newest kept
    assert "increment 0 " not in section               # oldest elided


def test_unreadable_delivery_block_degrades_to_stated_gap():
    """Constitution VI: a malformed record states the gap; it is never dropped
    silently and never raises into the dispatch path."""
    rec = parse_record("", "", None)
    assert rec.readable is False

    section = render([rec])
    assert "unreadable" in section
    assert "treat its outcome as unknown" in section


def test_parse_record_never_raises_on_garbage():
    """The renderer sits on the dispatch path — no input may wedge it."""
    for body in ("", "\x00\x00", "PR:", "Verify gate `: PASSED", "Error:"):
        rec = parse_record("obj", body, None)
        assert isinstance(rec, IncrementRecord)
        assert isinstance(render([rec]), str)


def test_missing_settlement_status_renders_unrecorded_not_a_guess():
    """The terminal status is devclaw's to state; absent, the section says so
    rather than inferring 'done' from the presence of a PR."""
    rec = parse_record("add the /health endpoint", SHIPPED_BODY, None)
    section = render([rec])

    assert "status=unrecorded" in section
    assert "status=done" not in section


def _brief_with(section: str) -> str:
    from devclaw.advance_brief import ADVANCE_BRIEF_MARKER

    return "\n".join([
        ADVANCE_BRIEF_MARKER + ", shippable increment using speckit, then stop.",
        "",
        "Goal: drive the demo repo to done",
        "",
        section,
    ])


def test_display_goal_annotates_prior_increments_with_their_count():
    """#547/#550: the section is worker INPUT. Human surfaces render the
    objective plus a COUNTED annotation, never the raw section."""
    section = render([
        parse_record("add /health", SHIPPED_BODY, "done"),
        parse_record("migrate the store", FAILED_BODY, "failed"),
    ])

    shown = display_goal(_brief_with(section))

    assert shown == "drive the demo repo to done  [+2 prior increment(s)]"
    assert "status=done" not in shown
    assert "github.com" not in shown


def test_display_goal_does_not_annotate_a_first_increment():
    """The section is on EVERY advance (FR-004 states the absence), so
    annotating its presence would decorate every dispatch identically and tell
    a reader nothing. Zero prior increments ⇒ no annotation — this keeps the
    #550 contract (``next`` is the bare objective) intact for a fresh goal."""
    shown = display_goal(_brief_with(render([])))

    assert shown == "drive the demo repo to done"
