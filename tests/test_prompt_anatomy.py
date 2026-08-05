"""Byte-anatomy of a cognition prompt — segmenting the saved blob into
instruction vs re-fed-data sections so the console can show WHAT fills a 105 KB
prompt (structural-root-2026-08-05, the visibility half). Pure post-hoc decoder;
these tests pin the segmentation, the instruction/data classification, and the
one accuracy trap: a re-fed section's OWN ``## `` sub-headers must not shred it.
"""

from __future__ import annotations

from devclaw.goal.evaluator import build_prompt as build_evaluator_prompt
from devclaw.goal.models import Goal, GoalStatus
from devclaw.server.prompt_anatomy import Anatomy, anatomize, to_dict


def _goal():
    return Goal(
        id="g", objective="ship a health endpoint", cadence="1d", engine="devclaw",
        workspace_dir="/ws", done_when="/health returns 200 and is tested",
        backlog=["add /health"],
    )


# ---- segmentation + classification -----------------------------------------


def test_splits_top_level_sections_and_classifies_instruction_vs_data():
    prompt = (
        "You are the evaluator. Judge hard.\n"
        "\n## Goal\nobjective: x\n"
        "\n## Recent event log\n" + ("log line\n" * 10) +
        "\n## What has actually shipped (grounded deliveries)\n" + ("a PR\n" * 10)
    )
    a = anatomize(prompt, role="evaluator")
    headers = [s.header for s in a.sections]
    assert headers[0] == "(instruction template)"   # preamble = what we author
    assert "Goal" in headers
    assert "Recent event log" in headers
    assert "What has actually shipped" in headers    # parenthetical gloss dropped

    by_header = {s.header: s for s in a.sections}
    assert by_header["Goal"].category == "instruction"
    assert by_header["Recent event log"].category == "data"
    assert by_header["Recent event log"].data_kind == "log"
    assert by_header["What has actually shipped"].data_kind == "deliveries"
    # chars sum to the whole prompt (no bytes lost or double-counted)
    assert sum(s.chars for s in a.sections) == len(prompt) == a.total_chars
    assert a.instruction_chars + a.data_chars == a.total_chars


def test_refed_data_dominates_a_real_done_gate_prompt():
    """The whole point: on a real done-gate prompt with a fat log + deliveries +
    review transcript, the DATA outweighs the instructions — that's the 105 KB
    made legible."""
    prompt = build_evaluator_prompt(
        _goal(), GoalStatus(),
        recent_log="event\n" * 4000,          # ~fat log
        deliveries="delivered a PR\n" * 2000,  # ~fat deliveries
        review_report="## Per-clause evidence\n" + ("the repo has /health\n" * 2000),
        at_done_gate=True,
    )
    a = anatomize(prompt, role="evaluator")
    kinds = {s.data_kind for s in a.sections if s.category == "data"}
    assert {"log", "deliveries", "review_report"} <= kinds
    # the re-fed state is the majority of the bytes — the finding we want visible
    assert a.data_chars > a.instruction_chars


# ---- the accuracy trap ------------------------------------------------------


def test_inner_headers_in_the_review_transcript_do_not_shred_the_section():
    """The captured worker transcript carries its OWN ``## Per-clause evidence`` /
    ``## Summary`` / ``## Structural health`` headers. Those are NOT top-level
    prompt sections — they must stay INSIDE the one 'Fresh read-only review' data
    section, or the byte attribution for the section we care about is wrong."""
    # ONE per-clause block (so _extract_review_report keeps it whole) followed by
    # the transcript's own ## Summary / ## Structural health sub-headers.
    review = (
        "## Per-clause evidence\n" + ("clause ok\n" * 400) +
        "## Summary\nlooks done\n"
        "## Structural health\nclean\n"
    )
    prompt = build_evaluator_prompt(
        _goal(), GoalStatus(), recent_log="x", deliveries="y",
        review_report=review, at_done_gate=True,
    )
    a = anatomize(prompt, role="evaluator")
    review_sections = [s for s in a.sections if s.data_kind == "review_report"]
    # exactly ONE review section — the inner ## headers were absorbed, not split
    assert len(review_sections) == 1
    # and it holds the whole transcript, not just the slice up to "## Summary"
    assert review_sections[0].chars > len(review) * 0.8
    # no inner header leaked out as its own top-level section
    assert not any(s.header in ("Per-clause evidence", "Summary", "Structural health")
                   for s in a.sections)


# ---- graceful degradation + shape ------------------------------------------


def test_prompt_with_no_known_headers_is_one_instruction_span():
    prompt = "TICKET: do the thing\nDIFF UNDER REVIEW:\n+ x\n"   # review-gate shape
    a = anatomize(prompt)
    assert len(a.sections) == 1
    assert a.sections[0].category == "instruction"
    assert a.data_chars == 0
    assert a.instruction_chars == len(prompt)


def test_empty_prompt_is_empty_anatomy():
    a = anatomize("")
    assert a == Anatomy(0, 0, 0, [])


def test_to_dict_is_camelcase_json_shape():
    a = anatomize("head\n\n## Recent event log\nx\n")
    d = to_dict(a)
    assert set(d) == {"totalChars", "instructionChars", "dataChars", "sections"}
    assert d["sections"][-1]["dataKind"] == "log"
    assert d["sections"][-1]["category"] == "data"
    assert all({"header", "chars", "category", "dataKind"} == set(s) for s in d["sections"])
