"""Named regression: the #227 anti-inference grounding clause is pinned in
the TEMPLATES themselves. The rest of the suite asserts caller-injected
markers (`REPOSITORY CONTEXT (facts`, section headers) — not the templates'
own grounding text — so before the prompt-concision pass nothing would have
caught a rewrite that silently dropped the clause. This pins it per template.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "devclaw"

# decomposer.md / firming.md rows were removed with the host-cognition chain
# (spec 008 shrink — the templates themselves are deleted).
_TEMPLATES = {
    _ROOT / "prompts" / "goal-evaluator.md": [
        "Ground every repository fact in what you are given",
        "working directory",
        "repository you have seen before",
    ],
    _ROOT / "quality" / "prompts" / "review-gate.md": [
        "Ground every repo fact in what you are given",
        "working directory",
        "repository you have seen before",
    ],
    _ROOT / "prompts" / "intake-readiness.md": [
        "Ground every repo fact in the Repository context",
        "working directory",
        "repository you have seen before",
    ],
    _ROOT / "prompts" / "self-triage.md": [
        "Ground every claim in the Repository context",
        "working directory",
        "repository you have seen before",
    ],
}


def test_repo_reasoning_templates_carry_the_anti_inference_grounding_clause():
    for path, markers in _TEMPLATES.items():
        raw = path.read_text(encoding="utf-8")
        for marker in markers:
            assert marker in raw, f"{path.name} lost grounding marker: {marker!r}"
