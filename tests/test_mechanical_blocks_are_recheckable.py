"""Structural tripwire: a ``mechanical:*`` block always has a way back.

``blocked_kind="mechanical:<site>"`` is a PROMISE — the condition is cheaply
re-checkable without an LLM, so the goal is parked, not abandoned. A mechanical
kind with neither an auto-heal branch nor a declared human-gated disposition
breaks that promise silently: the goal parks forever, ``resume_goal`` re-enters
the same condition, and nothing in the stubbed suite notices because no test
asserts the absence of a code path.

That is not hypothetical. ``mechanical:slice_hold`` shipped with no heal branch
and held two finance-sentry goals for four days
(specs/tiny/slice-guard-observes-the-goal); the same scan caught
``mechanical:corrupt_doc``, whose raise site had documented a self-heal since
spec 021 FR-004 that was never wired.

This guard is deliberately structural rather than behavioural: it reads the
source, so it covers kinds no test happens to exercise — which is exactly the
class that bit us.
"""
from __future__ import annotations

import ast
import pathlib

from devclaw.goal.tick import HUMAN_GATED_MECHANICAL_KINDS

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "devclaw"
_TICK = _PKG / "goal" / "tick.py"


def _written_mechanical_kinds() -> set[str]:
    """Every ``mechanical:*`` literal the package can assign to a blocked_kind.

    Scans string constants rather than ``blocked_kind=`` keywords on purpose:
    a kind routed through a variable or a helper still reaches the store, and
    the point is to catch the kind that ships without anyone wiring its exit.
    The bare ``"mechanical:"`` prefix used for ``startswith`` classification is
    not a kind and is excluded.
    """
    kinds: set[str] = set()
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("mechanical:")
                and node.value != "mechanical:"
            ):
                kinds.add(node.value)
    return kinds


def _healed_mechanical_kinds() -> set[str]:
    """Kinds tick.py's auto-heal dispatch actually branches on.

    Reads the ``status.blocked_kind == "mechanical:x"`` comparisons in the heal
    block, so a branch deleted in a refactor stops counting immediately.
    """
    tree = ast.parse(_TICK.read_text(encoding="utf-8"), filename=str(_TICK))
    healed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        if (
            isinstance(left, ast.Attribute)
            and left.attr == "blocked_kind"
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
            and right.value.startswith("mechanical:")
        ):
            healed.add(right.value)
    return healed


def test_every_mechanical_kind_heals_or_is_declared_human_gated() -> None:
    written = _written_mechanical_kinds()
    healed = _healed_mechanical_kinds()
    stranded = sorted(written - healed - set(HUMAN_GATED_MECHANICAL_KINDS))
    assert not stranded, (
        f"mechanical block kind(s) with no way back: {stranded}. "
        "`mechanical:` promises a cheap re-check, so each kind must either get "
        "an auto-heal branch in devclaw/goal/tick.py or be added to "
        "tick.HUMAN_GATED_MECHANICAL_KINDS with a reason a human must act. "
        "A kind with neither parks its goals permanently."
    )


def test_human_gated_declaration_has_no_dead_entries() -> None:
    """The declaration is a fact about the code, so it rots the moment a kind
    is retired. A stale entry would silently license a future kind of the same
    name to ship with no heal."""
    written = _written_mechanical_kinds()
    dead = sorted(set(HUMAN_GATED_MECHANICAL_KINDS) - written)
    assert not dead, (
        f"HUMAN_GATED_MECHANICAL_KINDS names kind(s) nothing writes: {dead}. "
        "Remove them — the list must describe the code as it is."
    )


def test_a_kind_that_heals_is_not_also_declared_human_gated() -> None:
    """Both dispositions at once is a contradiction: the heal would clear a
    block the declaration says only a human may clear."""
    both = sorted(_healed_mechanical_kinds() & set(HUMAN_GATED_MECHANICAL_KINDS))
    assert not both, (
        f"kind(s) both auto-healed and declared human-gated: {both}. "
        "Pick one — the auto-heal wins at runtime, so the declaration is a lie."
    )


def test_the_retired_slice_hold_kind_is_gone() -> None:
    """Named regression: the brake whose dead end motivated this guard must not
    come back without an exit. Retired by
    specs/tiny/slice-guard-observes-the-goal."""
    assert "mechanical:slice_hold" not in _written_mechanical_kinds(), (
        "mechanical:slice_hold was retired — the dispatch boundary no longer "
        "predicts build-ahead from sibling spec dirs."
    )
