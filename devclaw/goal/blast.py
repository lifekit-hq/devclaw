"""Blast radius (issue 979): rule-based, no model call. The safety pillar's
rule for the merge: hard-to-reverse work waits for the owner's ``merge``."""

from __future__ import annotations

import re

_HARD_WORDS = re.compile(r"\b(migrat\w*|delet\w*|drop\s+(table|column|index|database)|truncate|purge|wipe|destroy"
                         r"|prod(uction)?\s+(data|database|db))\b", re.I)
_HARD_FILES = re.compile(r"(^|/)(migrations?|alembic)/|\.sql$", re.I)
_WIDE = re.compile(r"\b(schema|shared|public\s+api)\b|(^|/)(schema|shared|common|utils?)[/.]", re.I)
_MERGE = re.compile(r"(decide:?\s*)?merge[.!]?", re.I)


def classify(done_when: str, files: "tuple[str, ...]") -> str:
    """``hard`` | ``wide`` | ``reversible`` from the Done-when text and the PR's paths."""
    if _HARD_WORDS.search(done_when) or any(_HARD_FILES.search(f) for f in files):
        return "hard"
    return "wide" if _WIDE.search(done_when) or any(_WIDE.search(f) for f in files) else "reversible"


def is_merge_decision(text: str, mention: str) -> bool:
    """The owner's whole answer is ``merge`` (after the mention, optionally ``decide:``)."""
    return bool(_MERGE.fullmatch(text.lower().replace(mention.lower(), "").strip()))
