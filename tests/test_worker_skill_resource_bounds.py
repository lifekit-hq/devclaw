"""The canonical verify skill carries the resource-bounding guidance
(spec 020 US3) — in its ONE home, runner/skills/ (constitution II).

Presence AND absence pinned per the prompt-content test rule: the guidance
names the declared env pair and the host-lying trap, and the bounded-memory-
first ruling; the raw template proves the markers weren't already there as
canned examples.
"""

from pathlib import Path

_SKILL = (
    Path(__file__).resolve().parents[1]
    / "runner" / "skills" / "_writes-code" / "40-verify-iterate.md"
)


def test_verify_skill_bounds_tooling_by_the_declared_allocation():
    text = _SKILL.read_text()
    assert "DEVCLAW_SANDBOX_MEMORY" in text
    assert "DEVCLAW_SANDBOX_CPUS" in text
    # the trap, by name — the false diagnosis path from the incident
    assert "/proc/meminfo" in text and "nproc" in text
    # the operator ruling: bounded-memory-first, wall-clock second
    assert "slower run that stays inside the cap" in text
    # and the adaptive signal: Killed means the cap, not a flake
    assert "Killed" in text


def test_no_other_skill_duplicates_the_resource_guidance():
    # One home for worker-kind instructions (constitution II): the sizing
    # guidance lives in the verify skill ONLY — a second copy is a silent
    # fork (#610's class).
    skills_root = _SKILL.parents[1]
    offenders = [
        p for p in skills_root.rglob("*.md")
        if p != _SKILL and "DEVCLAW_SANDBOX_MEMORY" in p.read_text()
    ]
    assert offenders == []
