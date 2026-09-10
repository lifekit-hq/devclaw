"""If a spec is in the tree, it is alive.

``specs/`` is this repo's direction memory, and it is read as a work queue —
by the owner at ``/devclaw-morning``, and by anyone asking "what is left".
A directory that sits there describing work nobody intends to do is not a
harmless archive: it reads as schedulable. Spec 007 looked schedulable for
six weeks after it was parked, and spec 036's US3 stopped its own clock by
hiding ``SPECIFIED, NOT IMPLEMENTED`` inside a user-story heading, where a
header sweep could not see it.

So the estate carries one closed vocabulary and one shape:

* In the tree, a spec is ``SHIPPED``, ``PARTIAL`` or ``DRAFT`` — nothing else.
* ``PARTIAL`` and ``DRAFT`` are non-terminal, so each names an owner AND a
  date. This is the label-that-stops-a-clock rule (``~/memory/README.md``
  rule 4): a state with no clock is how six weeks pass.
* ``CUT`` and ``SUPERSEDED`` are terminal, so they are NOT in the tree at
  all. They are one row each in ``specs/README.md`` and git is the archive —
  the same disposition ``docs/proposals/`` and ``docs/decisions/`` got on
  2026-09-06.
* A user-story heading never carries a status word. A story that is not
  being built is CUT in the ledger or lifts the whole spec to ``PARTIAL``.
* Every number ever issued resolves: a directory, or a ledger row. Numbers
  are identity, never position — they are never reused and never renumbered.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPECS = _ROOT / "specs"
_LEDGER = _SPECS / "README.md"

# The closed vocabulary. In-tree means alive.
_ALIVE = ("SHIPPED", "PARTIAL", "DRAFT")
_TERMINAL = ("CUT", "SUPERSEDED")
# Words that let a spec — or one story inside it — stop its own clock.
_CLOCKLESS = ("PARKED", "SUSPENDED", "DEFERRED", "NOT IMPLEMENTED", "NOT SCHEDULED")
# Whole words only: "execution" and "executable" both contain "cut".
_STOP_RE = {w: re.compile(rf"\b{w}\b") for w in _CLOCKLESS + _TERMINAL}

_STATUS_RE = re.compile(r"^\*\*Status\*\*:\s*(?P<word>[A-Z]+)\b(?P<rest>.*)$", re.M)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_OWNER_RE = re.compile(r"\bowner:\s*\S+", re.I)
_STORY_RE = re.compile(r"^#{2,4}\s*User Story\b.*$", re.M)
_LEDGER_ROW_RE = re.compile(r"^\|\s*(?P<num>\d{3})\s*\|\s*(?P<word>[A-Z]+)\s*\|", re.M)


def _spec_dirs() -> "list[Path]":
    return sorted(d for d in _SPECS.iterdir() if d.is_dir() and re.fullmatch(r"\d{3}-.+", d.name))


def _header(spec: Path) -> str:
    """The block above the first prose section — where status is declared."""
    text = spec.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n## ")
    return head


@pytest.mark.parametrize("spec_dir", _spec_dirs(), ids=lambda d: d.name)
def test_a_spec_in_the_tree_declares_one_living_status(spec_dir: Path) -> None:
    spec = spec_dir / "spec.md"
    assert spec.is_file(), f"{spec_dir.name} has no spec.md"

    found = _STATUS_RE.findall(_header(spec))
    assert len(found) == 1, (
        f"{spec_dir.name}: expected exactly one '**Status**: WORD' line in the header, "
        f"found {len(found)}. One spelling, one place."
    )
    word, rest = found[0]
    assert word in _ALIVE, (
        f"{spec_dir.name}: status {word!r} is not in the living vocabulary {_ALIVE}. "
        f"{_TERMINAL} are ledger rows in specs/README.md, not directories."
    )
    if word in ("PARTIAL", "DRAFT"):
        assert _OWNER_RE.search(rest), (
            f"{spec_dir.name}: {word} is non-terminal and must name an owner "
            f"(`owner: <name>`) — a state with no owner is nobody's."
        )
        assert _DATE_RE.search(rest), (
            f"{spec_dir.name}: {word} is non-terminal and must carry a date — "
            f"build-or-cut by YYYY-MM-DD. A label with no clock stops one."
        )


@pytest.mark.parametrize("spec_dir", _spec_dirs(), ids=lambda d: d.name)
def test_a_user_story_never_carries_its_own_status(spec_dir: Path) -> None:
    """036 US3 suspended itself in a heading and no header sweep saw it."""
    for heading in _STORY_RE.findall((spec_dir / "spec.md").read_text(encoding="utf-8")):
        upper = heading.upper()
        for stop, pat in _STOP_RE.items():
            assert not pat.search(upper), (
                f"{spec_dir.name}: user-story heading carries {stop!r}:\n  {heading.strip()}\n"
                f"A story that is not being built is a ledger row, or it lifts the "
                f"whole spec to PARTIAL with an owner and a date."
            )


@pytest.mark.parametrize("spec_dir", _spec_dirs(), ids=lambda d: d.name)
def test_a_status_line_never_stops_its_own_clock(spec_dir: Path) -> None:
    """`SHIPPED — except US3, which is PARKED` is the shape being forbidden.

    Only the status line itself is read. Prose further down that RECORDS a
    parking that was later reversed (spec 039's is the reversal that earned
    this carve-out) is history, and history is what a spec is for.
    """
    found = _STATUS_RE.findall(_header(spec_dir / "spec.md"))
    line = " ".join(found[0]).upper() if found else ""
    for stop in _CLOCKLESS:
        assert not _STOP_RE[stop].search(line), (
            f"{spec_dir.name}: status line carries {stop!r}. The vocabulary is "
            f"{_ALIVE} in the tree and {_TERMINAL} in the ledger — nothing else."
        )


def test_every_number_ever_issued_resolves() -> None:
    """A gap must be explained by the ledger, never by absence."""
    assert _LEDGER.is_file(), "specs/README.md is the ledger; it must exist"
    ledger = {m.group("num"): m.group("word") for m in _LEDGER_ROW_RE.finditer(_LEDGER.read_text(encoding="utf-8"))}
    in_tree = {d.name[:3] for d in _spec_dirs()}

    bad = {n: w for n, w in ledger.items() if w not in _TERMINAL + ("VOID",)}
    assert not bad, (
        f"ledger rows with a state outside {_TERMINAL + ('VOID',)}: {bad}. "
        f"A number is CUT, SUPERSEDED, or VOID (never issued)."
    )

    overlap = sorted(in_tree & set(ledger))
    assert not overlap, (
        f"numbers both in the tree and in the terminal ledger: {overlap}. "
        f"A CUT or SUPERSEDED spec leaves the tree; git is its archive."
    )

    highest = max(int(n) for n in in_tree | set(ledger))
    missing = [f"{n:03d}" for n in range(1, highest + 1) if f"{n:03d}" not in in_tree and f"{n:03d}" not in ledger]
    assert not missing, (
        f"numbers with neither a directory nor a ledger row: {missing}. "
        f"Every number ever issued resolves — that is what makes a gap readable."
    )
