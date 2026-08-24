"""Doctor report model — verdicts, findings, the report envelope.

In-memory only: doctor persists nothing (spec 016 FR-001). The report is
deterministic for unchanged state — findings carry no timestamps and keep a
fixed order (instance checks in declared order, then projects sorted by id) —
so two runs over the same state serialize byte-identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    #: the check could not execute — the error is the evidence. Reported,
    #: never omitted (FR-005: a crashed check is loud, not silent).
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Finding:
    check_id: str
    verdict: Verdict
    evidence: str
    #: existing recovery verb for non-ok verdicts (link_goal, resume_goal,
    #: clear_usage_pause, onboard, set_run_schedule, …); empty when ok.
    remedy: str = ""
    #: None for instance-section findings.
    project_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "remedy": self.remedy,
            "project_id": self.project_id,
        }


@dataclass
class DoctorReport:
    findings: list[Finding]

    @property
    def healthy(self) -> bool:
        return all(f.verdict is Verdict.OK for f in self.findings)

    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for f in self.findings:
            out[f.verdict.value] += 1
        return out

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }
