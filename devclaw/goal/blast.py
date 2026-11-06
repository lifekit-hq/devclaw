"""Blast radius (#979): hard-to-reverse work waits for the owner's ``merge``; ``wide`` is classified, not yet gated."""

import re

_HARD_WORDS = re.compile(r"\b(migrat\w*|delet\w*|drop\s+(table|column|index|database)|truncate|purge|wipe|destroy|prod(uction)?\s+(data|database|db))\b", re.I)
_HARD_FILES = re.compile(r"(^|/)(migrations?|alembic)/|\.sql$", re.I)
_WIDE = re.compile(r"\b(schema|shared|public\s+api)\b|(^|/)(schema|shared|common|utils?)[/.]", re.I)
_MERGE = re.compile(r"(decide:?\s*)?merge[.!]?", re.I)


def classify(done_when: str, files: "tuple[str, ...]") -> str:
    if _HARD_WORDS.search(done_when) or any(_HARD_FILES.search(f) for f in files): return "hard"
    return "wide" if _WIDE.search(done_when) or any(_WIDE.search(f) for f in files) else "reversible"


def is_merge_decision(text: str, mention: str) -> bool:
    return bool(_MERGE.fullmatch(text.lower().replace(mention.lower(), "").strip()))
