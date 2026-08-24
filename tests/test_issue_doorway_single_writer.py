"""Spec 014 SC-004 — the structural guard: exactly one code path in devclaw
creates GitHub issues from machine findings.

Walks every module under ``devclaw/`` and asserts the ``gh issue create``
subprocess invocation appears ONLY in the two doorways:

- ``devclaw/issue_doorway.py`` — machine findings (spec 014 FR-002)
- ``devclaw/intake.py`` — human asks (the single-intake-doorway, out of
  spec 014's scope by design)

The views-never-read-back move applied to filing: a new mechanism that shells
``gh issue create`` directly is a second writer the schema/dedup guarantees
don't cover, and this test makes it a loud failure instead of a silent fork.
Pattern follows ``tests/test_config_single_doorway.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

DEVCLAW = Path(__file__).resolve().parent.parent / "devclaw"

ALLOWED = {"issue_doorway.py", "intake.py"}


def _string_args(call: ast.Call) -> list[str]:
    out = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append(arg.value)
    return out


def _creates_issue(tree: ast.AST) -> bool:
    """True when any call carries the literal arg sequence gh, issue, create
    (the ``_run("gh", "issue", "create", …)`` shape) or builds the same list
    literal (``["gh", "issue", "create", …]``)."""
    for node in ast.walk(tree):
        strings: list[str] = []
        if isinstance(node, ast.Call):
            strings = _string_args(node)
        elif isinstance(node, (ast.List, ast.Tuple)):
            strings = [
                e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
        for i in range(len(strings) - 2):
            if strings[i : i + 3] == ["gh", "issue", "create"]:
                return True
    return False


def test_only_the_two_doorways_create_issues():
    offenders = []
    for path in sorted(DEVCLAW.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _creates_issue(tree) and path.name not in ALLOWED:
            offenders.append(str(path.relative_to(DEVCLAW.parent)))
    assert offenders == [], (
        "machine findings must be filed through devclaw/issue_doorway.py "
        f"(human asks through devclaw/intake.py) — direct `gh issue create` "
        f"found in: {offenders}"
    )


def test_the_guard_still_sees_the_doorways():
    """The allowlist is not vacuous: both doorways really do create issues —
    if one stops, the guard must shrink, not silently pass."""
    for name in sorted(ALLOWED):
        tree = ast.parse((DEVCLAW / name).read_text(encoding="utf-8"))
        assert _creates_issue(tree), f"{name} no longer creates issues — update the guard"
