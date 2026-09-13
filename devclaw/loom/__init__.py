"""loom — the engine-agnostic substrate: the usage-limit classifier, the
test-integrity guard, the untrusted-content fence. A leaf: imports nothing
from the rest of devclaw (the import-linter contract gates it)."""

from __future__ import annotations

from .limits import (
    Classification,
    FailureKind,
    PAUSING_KINDS,
    RETRY_NOW_KINDS,
    classify_failure,
    pause_seconds,
)
from .test_integrity import IntegrityReport, scan_diff

__all__ = [
    "classify_failure", "pause_seconds", "FailureKind", "Classification",
    "PAUSING_KINDS", "RETRY_NOW_KINDS", "scan_diff", "IntegrityReport",
]
