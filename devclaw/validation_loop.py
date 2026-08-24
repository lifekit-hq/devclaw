"""The live-validation loop's mechanical substrate (spec 015).

Root module on purpose (beside ``task_change.py`` and ``issue_doorway.py``):
both the settle path (layer 4, after a ``validate_product`` run) and the goal
layer (layer 2, the deploy trigger and prod smoke) consume it, and neither may
reach through the other. Everything here is zero-LLM: pure mapping from a
runner ``validation_report`` to spec-014 machine findings, the doorway filing
calls, the one-line run record, and the read-only prod smoke.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import urllib.request
from typing import Optional, Sequence

from . import issue_doorway as _doorway
from .intake import repo_slug as _repo_slug

#: The two doorway sources this loop produces (spec 014 data-model names both).
VALIDATOR_SOURCE = "validator"
SMOKE_SOURCE = "deploy_smoke"

_EVIDENCE_TAIL = 2000


def repo_slug_for_workspace(workspace_dir: str) -> Optional[str]:
    """``owner/name`` from the workspace's origin remote — the repo findings
    file against. None when there is no usable remote (dev/stub repos file
    nowhere; the caller logs that loudly instead of guessing)."""
    try:
        proc = subprocess.run(
            ["git", "-C", workspace_dir, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _repo_slug(proc.stdout.strip() or None)


def _tail(step: Optional[dict]) -> str:
    return ((step or {}).get("output_tail") or "").strip() or "unknown"


def findings_from_report(report: object) -> list["_doorway.MachineFinding"]:
    """Map one runner ``validation_report`` to spec-014 findings — the
    data-model.md table, verbatim. A missing/empty report maps to one
    run-crashed finding rather than silence (FR-006)."""
    if not isinstance(report, dict):
        return [_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint="validator|no-report",
            title="validation run produced no report",
            evidence="the validate_product runner returned no validation_report",
            expected="a validation_report from every triggered run",
            actual="none returned", severity="high",
            proposed_done_when=(
                "A triggered validation run returns a validation_report and "
                "its outcome is visible in the run record."
            ),
        )]
    note = (report.get("note") or "").strip()
    boot = report.get("boot")
    suites = report.get("suites")

    if note.startswith("missing contract"):
        return [_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint="validator|missing-contract",
            title="validation triggered but no contract is declared",
            evidence=note,
            expected="devclaw.json declares validation.boot and validation.suites",
            actual="no usable validation contract reached the run",
            severity="high",
            proposed_done_when=(
                "devclaw.json on the default branch declares a validation "
                "contract (boot + suites) and a triggered run boots it green."
            ),
        )]
    if boot is not None and not boot.get("passed"):
        return [_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint="validator|boot",
            title="product failed to boot for validation",
            evidence=f"{note}\n\nboot output tail:\n{_tail(boot)}",
            expected="the declared boot command brings up a hermetic seeded instance (exit 0)",
            actual=f"boot exited {boot.get('exit_code')!r} (timed_out={bool(boot.get('timed_out'))})",
            severity="critical",
            proposed_done_when=(
                "The declared validation.boot command exits 0 with the product "
                "up, and a full validation run completes against it."
            ),
        )]
    if boot is None:
        # infra before boot (e.g. toolchain provisioning) — same class as boot
        return [_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint="validator|boot",
            title="validation infrastructure failed before boot",
            evidence=note or "unknown",
            expected="the sandbox provisions and the boot command runs",
            actual=note or "unknown", severity="critical",
            proposed_done_when=(
                "A triggered validation run reaches the suites step and its "
                "outcome is visible in the run record."
            ),
        )]

    findings: list[_doorway.MachineFinding] = []
    for title in report.get("failing_tests") or []:
        t = str(title).strip()
        if not t:
            continue
        findings.append(_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint=f"validator|{t}",
            title=f"acceptance scenario failing: {t}",
            evidence=(
                f"suites exit {suites.get('exit_code') if suites else 'unknown'}; "
                f"failing scenario: {t}\n\nsuites output tail:\n{_tail(suites)}"
            ),
            expected=f"acceptance scenario '{t}' passes against the running product",
            actual="the scenario failed in the live-validation run",
            severity="high",
            spec_ref=t,
            proposed_done_when=(
                f"Acceptance scenario '{t}' passes in a validation run against "
                "the hermetically booted product."
            ),
        ))
    if not findings and suites is not None and not suites.get("passed"):
        findings.append(_doorway.MachineFinding(
            source=VALIDATOR_SOURCE, fingerprint="validator|suite-exit",
            title="acceptance suites failed (no per-scenario report)",
            evidence=f"suites exited {suites.get('exit_code')!r}\n\noutput tail:\n{_tail(suites)}",
            expected="the declared validation.suites command exits 0",
            actual=f"exit {suites.get('exit_code')!r} (timed_out={bool(suites.get('timed_out'))})",
            severity="high",
            proposed_done_when=(
                "The declared validation.suites command passes in a validation "
                "run against the hermetically booted product."
            ),
        ))
    return findings


async def file_validation_findings(
    store,
    repo: str,
    findings: Sequence["_doorway.MachineFinding"],
    *,
    gh=None,
    now_ms: Optional[int] = None,
) -> list["_doorway.FilingOutcome"]:
    """File each finding through the spec-014 doorway. Outcomes come back in
    order; a `failed` outcome is already loud (doorway FR-006)."""
    outcomes = []
    for finding in findings:
        outcomes.append(await _doorway.file_finding(
            finding, repo=repo, store=store, gh=gh, now_ms=now_ms,
        ))
    return outcomes


def run_record_line(report: object, outcomes: Sequence) -> str:
    """The one-line run record (US2 scenario 3 — a run record, not silence)."""
    if not isinstance(report, dict):
        return "validation: run produced no report (finding filed)"
    note = (report.get("note") or "").strip()
    issues = ", ".join(
        f"#{o.issue_number}" for o in outcomes if getattr(o, "issue_number", None)
    )
    if outcomes:
        head = f"validation: {len(outcomes)} finding(s) filed"
        if issues:
            head += f" ({issues})"
        return f"{head}{' — ' + note if note else ''}"
    br = report.get("browser_report") or {}
    executed = int(br.get("expected", 0)) + int(br.get("unexpected", 0)) + int(br.get("flaky", 0))
    if note:
        return f"validation: green — {note}"
    return f"validation: green ({executed} executed)"


# ---- the read-only prod smoke (FR-009) --------------------------------------

def prod_smoke(base_url: str, smoke_path: str = "/", *, timeout_s: float = 10.0) -> Optional[str]:
    """GET ``base_url + smoke_path`` read-only; None = healthy, else the
    actionable failure reason. Never raises; never anything but GET."""
    url = base_url.rstrip("/") + smoke_path
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 — loopback/tailnet URL from our own deploy
            code = getattr(resp, "status", 200)
    except Exception as exc:  # noqa: BLE001 — the reason IS the finding
        return f"GET {url} failed: {type(exc).__name__}: {exc}"
    if 200 <= code < 400:
        return None
    return f"GET {url} returned HTTP {code}"


def smoke_finding(slug: str, smoke_path: str, reason: str) -> "_doorway.MachineFinding":
    return _doorway.MachineFinding(
        source=SMOKE_SOURCE,
        fingerprint=f"deploy_smoke|{slug}|{smoke_path}",
        title=f"post-deploy smoke failed on {smoke_path}",
        evidence=reason[:_EVIDENCE_TAIL],
        expected=f"GET {smoke_path} on the deployed instance responds 2xx/3xx",
        actual=reason[:200],
        severity="critical",
        proposed_done_when=(
            f"A deploy of {slug} completes with the read-only smoke on "
            f"{smoke_path} responding 2xx/3xx."
        ),
    )


async def run_prod_smoke(store, *, slug: Optional[str], base_url: str,
                         smoke_path: str = "/", gh=None) -> Optional[str]:
    """The whole post-deploy smoke edge: probe, and file on failure when the
    repo slug is known. Returns the failure reason (also logged loudly) or
    None when healthy. Best-effort toward the deploy itself — a smoke problem
    never un-deploys anything."""
    reason = await asyncio.to_thread(prod_smoke, base_url, smoke_path)
    if reason is None:
        return None
    sys.stderr.write(f"deploy-smoke: {reason}\n")
    if slug:
        await _doorway.file_finding(
            smoke_finding(slug, smoke_path, reason), repo=slug, store=store, gh=gh,
        )
    return reason
