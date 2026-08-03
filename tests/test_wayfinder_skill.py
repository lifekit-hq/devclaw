"""The wayfinder worker skill (openhands-runner/skills/wayfinder/00-maintain-map.md)
teaches the sandbox worker to WRITE the plan-map; devclaw.goal.wayfinder.parse_map
READS it. They are two ends of ONE contract — this pins them together so the skill
can't silently drift from the parser (a drifted skill = the worker emits a map the
control plane can't read, and the goal silently can't be driven)."""

from __future__ import annotations

import typing
from pathlib import Path

from devclaw.goal.wayfinder import MAP_LABEL, TicketKind

_SKILL = (
    Path(__file__).resolve().parent.parent
    / "openhands-runner" / "skills" / "wayfinder" / "00-maintain-map.md"
)


def _text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_skill_file_exists():
    assert _SKILL.is_file(), f"missing worker skill at {_SKILL}"


def test_skill_teaches_the_map_label():
    assert MAP_LABEL in _text()  # "wayfinder:map" — parser's map index label


def test_skill_names_every_ticket_kind_the_parser_accepts():
    text = _text()
    for kind in typing.get_args(TicketKind):  # research/prototype/grilling/task
        assert kind in text, f"skill does not teach the wayfinder kind {kind!r}"


def test_skill_teaches_the_body_sections_the_parser_reads():
    text = _text()
    # parse_map extracts these via _section(body, header)
    for header in ("## Destination", "## Notes", "## Out of scope"):
        assert header in text, f"skill omits the {header!r} section the parser reads"


def test_skill_teaches_the_dependency_and_resolution_markers():
    text = _text().lower()
    assert "blocked by #" in text  # parser: _BLOCKED_BY_RE
    assert "resolution:" in text   # parser: _RESOLUTION_RE
