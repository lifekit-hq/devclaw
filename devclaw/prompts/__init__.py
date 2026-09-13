"""Prompt templates as markdown files, rendered with ``str.format``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _read(slug: str) -> str:
    return (_DIR / f"{slug}.md").read_text(encoding="utf-8").rstrip()


def load_prompt(slug: str, /, **vars: object) -> str:
    """Render ``devclaw/prompts/<slug>.md``; literal braces are ``{{ }}``."""
    return _read(slug).format(**vars)
