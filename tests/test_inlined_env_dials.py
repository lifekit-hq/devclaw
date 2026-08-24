"""Regression: the speculative tuning dials inlined in #410 stay inlined.

#410 removed a cluster of ``DEVCLAW_*`` env vars that were pure internal tuning
knobs — read in exactly one place, never set off-default in any prod ``.env`` or
shakedown. Each became a plain module constant (tuned by PR, not env). These
tests pin two things so the sprawl doesn't creep back:

  1. none of the inlined names is read from the environment anywhere in source,
  2. the surviving constants hold the exact defaults the env vars used to carry
     (so the inline was behaviour-preserving).

The general code↔doc parity is enforced separately by
``test_env_vars_doc_sync.py``; this test guards the specific #410 removals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devclaw import task_git, task_queue
from devclaw import quality
from devclaw.queue import settle as queue_settle

_REPO = Path(__file__).resolve().parents[1]

#: same env-READ shape the doc-sync test scans for (tolerates a line break
#: between the call and the string literal).
_ENV_READ = re.compile(
    r"""(?:environ(?:\.get|\.setdefault)?|getenv)\s*[\(\[]\s*["'](DEVCLAW_[A-Z_]+)["']""",
)

#: the env vars #410 inlined — none may be read from the environment again.
_INLINED = {
    "DEVCLAW_GOAL_BROWSER_GATE_MODE",
    "DEVCLAW_GOAL_BROWSER_REACHABILITY",
    "DEVCLAW_BRANCH_STALE_THRESHOLD",
    "DEVCLAW_REVIEW_DEGRADE",
    "DEVCLAW_REVIEW_DEGRADE_MAX_FILES",
}


def _source_files() -> list[Path]:
    files = list((_REPO / "devclaw").rglob("*.py"))
    files += list((_REPO / "runner").glob("*.py"))
    return files


def test_inlined_dials_are_no_longer_read_from_env():
    offenders: dict[str, list[str]] = {}
    for path in _source_files():
        for name in _ENV_READ.findall(path.read_text(encoding="utf-8")):
            if name in _INLINED:
                offenders.setdefault(name, []).append(str(path.relative_to(_REPO)))
    assert not offenders, (
        f"#410 inlined these dials — they must not be read from env again: {offenders}"
    )


@pytest.mark.parametrize(
    "obj, attr, expected",
    [
        (task_queue, "BROWSER_GATE_MODE", "flexible"),
        (queue_settle, "BROWSER_REACHABILITY_ENABLED", True),
        (task_git, "BRANCH_STALE_THRESHOLD", 50),
        (quality, "_DEGRADE_ENABLED", True),
        (quality, "_DEGRADE_MAX_FILES_DEFAULT", 40),
    ],
)
def test_inlined_constants_preserve_the_former_env_defaults(obj, attr, expected):
    assert getattr(obj, attr) == expected


def test_degrade_helpers_read_the_module_constants():
    """The helpers still exist (call sites unchanged) and now resolve to the
    module constants a PR/test flips, not the environment."""
    assert quality._degrade_enabled() is True
    assert quality._degrade_max_files() == 40
