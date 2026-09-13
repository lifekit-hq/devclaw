"""devclaw doctor — read-only, zero-cognition instance diagnostics. Every
check reports; a crashed check is an ``unknown`` finding, never an omission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .checks import CHECKS
from .model import DoctorReport, Finding, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from ..project_registry import ProjectRegistry
    from ..state_store import StateStore

__all__ = ["DoctorReport", "Finding", "Verdict", "run_doctor"]


def _run_one(fn: Callable, store, registry) -> list[Finding]:
    try:
        findings = fn(store, registry)
    except Exception as exc:  # noqa: BLE001 — loud, never omitted
        return [Finding(fn.__name__.removeprefix("check_"), Verdict.UNKNOWN, f"check crashed: {exc!r}")]
    if not findings:
        return [Finding(fn.__name__.removeprefix("check_"), Verdict.UNKNOWN, "check returned no finding")]
    return findings


def run_doctor(store: "StateStore", registry: "ProjectRegistry") -> DoctorReport:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(_run_one(check, store, registry))
    return DoctorReport(findings=findings)
