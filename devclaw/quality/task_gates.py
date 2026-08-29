"""Task-gate adapters — the settle path's gate chain, split out of task_queue.

The pure verdict helpers (``_verify_failure_summary``, ``_integrity_failure``,
``_has_playwright_config``, ``_browser_gate_failure``) and the six ``Gate``
adapters ``run_pipeline`` consumes. Single-writer stays in the TaskQueue — the
gates only READ run artifacts (the runner verify dict + the shared materialized
diff) and return verdicts; no row mutation, no store writes.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, cast

from .. import config as _config
from ..loom.declared_scope import scope_check, violation_summary
from ..loom.test_integrity import present_test_names, scan_diff
from .browser_gate import PLAYWRIGHT_CONFIG_NAMES, browser_run_verdict
from .gate_pipeline import GateInput, GateVerdict

if TYPE_CHECKING:
    from ..queue.settle import SettleMixin
    from ..state_store import TaskKind

#: Browser-E2E gate (2026-07-17): after verify + integrity + review pass, a change
#: that touched a web-UI path must have been exercised in a REAL browser (a passing
#: Playwright run, proven via the runner's `browser_report` counts) before it ships
#: — closing the hole where `ng build && vitest` + a static diff review pass while
#: the running app is broken (finance-sentry cmn-select threw NG05105 unopened).
#: Fail-open on capability uncertainty (a project with no browser suite, under
#: `flexible`), fail-closed on evidence (a failed/un-run suite).
BROWSER_GATE_ENABLED = _config.BROWSER_GATE_ENABLED
#: Stable prefix on the browser-gate feed-back reason (parallels the review/
#: integrity reasons) so the settle path and tests can recognise it.
_BROWSER_GATE_MARKER = "browser gate (failing closed):"


def _verify_failure_summary(verify: dict) -> str:
    """Human-readable failure reason for a task whose verify gate didn't pass —
    stored as the task error so a human (or a retry) can see what broke."""
    cmd = verify.get("cmd", "")
    if verify.get("timed_out"):
        head = f"verify gate timed out: `{cmd}`"
    else:
        head = f"verify gate failed (exit {verify.get('exit_code')}): `{cmd}`"
    out = (verify.get("output") or "").strip()
    return f"{head}\n{out[-1500:]}" if out else head


def _integrity_failure(diff: str, workspace_dir: Optional[str] = None) -> Optional[str]:
    """Return a failure summary if the change weakened the tests (deleted/skipped),
    else None. Enforces what the prompt only asks for. Operates on an already-
    computed diff (shared with the review gate). A CRASH in the scanner fails
    CLOSED: a quality gate that silently no-ops on its own error is exactly how
    a gutted test suite ships unnoticed — the crash feeds the same retry loop
    as a real integrity failure, then escalates.

    Relocation credit (2026-07-17): a removed test whose name still exists as a
    test declaration ELSEWHERE in the post-change tree is a move/dedup, not a
    weakening — crediting it unblocks the legitimate "delete the old file whose
    methods a prior PR already ported" case that cost closeloop-bench ~40h of
    thrash. Grounded against the real tree via ``present_test_names``; only
    positive evidence relaxes, and a walk hiccup credits nothing (fail closed),
    so a genuine test deletion is never waved through."""
    try:
        report = scan_diff(diff)
    except Exception as err:  # noqa: BLE001 — fail closed, never silently approve
        return (
            f"test-integrity gate crashed (failing closed): "
            f"{err.__class__.__name__}: {err}. The change was not scanned for "
            "weakened tests, so it must not ship on the gate's silence."
        )
    if report.ok:
        return None
    if report.removed_tests > 0 and report.removed_names and workspace_dir:
        try:
            present = present_test_names(workspace_dir)
        except Exception:  # noqa: BLE001 — a walk hiccup must not mask a weakening
            present = set()
        # distinct removed names proven to live elsewhere, capped at the count.
        credited = min(
            report.removed_tests,
            len({n for n in report.removed_names if n in present}),
        )
        report.removed_tests = max(0, report.removed_tests - credited)
    if report.ok:
        return None
    return (
        f"{report.summary()}. The gate passed, but the change weakened the test "
        "suite — restore the tests and make the code genuinely pass them; do not "
        "delete, skip, or gut tests to go green."
    )


def _has_playwright_config(workspace_dir: str) -> bool:
    """Does the workspace carry a Playwright config anywhere near its roots — i.e.
    has the project opted into a browser suite at all? Bounded walk (skips
    node_modules/.git/dist and stops at depth 3) so a large monorepo stays cheap.
    This is the capability signal that separates ``never_ran`` (config exists, a
    run was expected, none happened) from ``absent`` (nothing to run)."""
    try:
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", ".angular", ".venv")]
            if root[len(workspace_dir):].count(os.sep) >= 3:
                dirs[:] = []
            if any(f in PLAYWRIGHT_CONFIG_NAMES for f in files):
                return True
    except OSError:
        return False
    return False


def _browser_gate_failure(
    verify: Optional[dict], diff: str, workspace_dir: str, *, mode: str,
    surface: Optional[str] = None,
) -> Optional[str]:
    """Return a feed-back reason if a web-UI change failed the browser-E2E gate
    CLOSED, else None. A change that touched a frontend path must carry a passing
    real-browser run (the runner's ``browser_report``); a failed or un-run suite
    is fed back through the SAME retry loop as an integrity/review failure so the
    agent adds/repairs the Playwright spec. Fail-open on capability uncertainty
    (a project with no browser suite, under ``flexible``); fail-closed on
    evidence. No cognition, no network — a bounded filesystem check + the pure
    verdict."""
    if not BROWSER_GATE_ENABLED:
        return None
    # Declared surface (spec 016 US2, devclaw.json read at the merged base):
    # 'library' = the whole repo is library surface — the gate's app-surface
    # expectation does not apply (the declared form of the path-glob
    # exemption); 'app' = every frontend path is app surface — the glob
    # exemption is disabled; None/undeclared = heuristics as before.
    if surface == "library":
        sys.stderr.write(
            "task-queue: browser-gate not applicable — devclaw.json declares "
            "surface=library (library build/test gates own this change)\n"
        )
        return None
    config_present = _has_playwright_config(workspace_dir)
    if surface == "app":
        verdict = browser_run_verdict(
            verify, diff, config_present=config_present, library_globs=(),
        )
    else:
        verdict = browser_run_verdict(verify, diff, config_present=config_present)
    if not verdict.blocks_delivery(mode):
        if verdict.state == "absent":
            sys.stderr.write(
                f"task-queue: browser-gate not enforced ({mode}) — {verdict.detail}\n"
            )
        return None
    return (
        f"{_BROWSER_GATE_MARKER} {verdict.detail}. A change touching the web UI must "
        "pass a real-browser end-to-end run (`npx playwright test --reporter=json`) "
        "before it ships — add or repair the Playwright spec that exercises this "
        "change in the running app, and make the verify gate run it."
    )


# ── Gate objects (#407 PR3) ─────────────────────────────────────────────────
# Thin, pure adapters that wrap the four existing gate functions/methods as
# `Gate` verdict producers for `run_pipeline`. Each one only READS the run
# artifacts (the runner verify dict + the shared diff) and returns a verdict —
# no row mutation, no store writes: the single-writer TaskQueue keeps every
# mark_*/requeue/set_global_pause. The wrapped functions are UNCHANGED, so the
# verdicts are byte-identical to the inline ladder they replace.
#
# `applies()` is uniformly True here: today all four gates always participate,
# self-skipping internally (a disabled/non-reviewable/scaffold/backend gate
# returns None → a passing verdict, exactly as the inline ladder did). The
# predicate is the real seam a future gate can use to opt out cheaply; the
# STRICT ORDERING (review only after integrity, browser only after both) comes
# from run_pipeline's short-circuit, not from applies().


@dataclass
class _VerifyGate:
    """Always-hard (ADR 0007) verify_cmd gate: the runner's own verify sub-result.
    Fails CLOSED on a ran-and-not-passed verify; never dial-able. Reads no diff,
    so a verify failure short-circuits the pipeline before any ``git diff`` runs."""

    gate_id: str = "verify"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        verify = gi.verify
        if verify and verify.get("ran") and not verify.get("passed"):
            return GateVerdict.failed(self.gate_id, _verify_failure_summary(verify))
        return GateVerdict.passed(self.gate_id)


@dataclass
class _IntegrityGate:
    """Always-hard test-integrity scan over the shared diff. Fails CLOSED (the
    dial never loosens it); never dial-able. First consumer of the shared diff."""

    gate_id: str = "test_integrity"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        diff = await gi.diff()
        failure = _integrity_failure(diff, gi.workspace_dir)
        if failure is not None:
            return GateVerdict.failed(self.gate_id, failure)
        return GateVerdict.passed(self.gate_id)


@dataclass
class _MaterializeGate:
    """The span itself — always-hard, zero-LLM (spec 013 FR-007).

    Not a judgement of the change: the precondition of every judgement below it.
    It asks :meth:`GateInput.change` for the materialized artifact and fails
    CLOSED when the change could not be determined at all. Before spec 013 that
    case degraded to ``""`` and every diff-reading gate below passed on it
    trivially — a gate shipping on its own silence, which #186 forbids.

    Placed immediately after ``verify`` and before every consumer of the span:
    a verify failure still short-circuits ahead of any git call, and no gate can
    ever see a pre-materialization view.
    """

    gate_id: str = "materialize"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        try:
            change = await gi.change()
        except Exception as err:  # noqa: BLE001 — undeterminable ⇒ fail closed
            return GateVerdict.failed(
                self.gate_id,
                f"the agent's change could not be determined "
                f"({err.__class__.__name__}: {err}) — failing closed: an "
                f"undeterminable span is not an empty one",
            )
        if change.is_error:
            return GateVerdict.failed(
                self.gate_id,
                f"the agent's change could not be determined: {change.reason} — "
                f"failing closed. Nothing may be judged or shipped on a span "
                f"that could not be captured.",
            )
        return GateVerdict.passed(self.gate_id)


@dataclass
class _ScopeGate:
    """Declared-file-scope gate (spec 010 FR-103) — always-hard, zero-LLM.

    A `[P]` task declares the paths it will touch; this is where that
    declaration stops being a promise. It reads
    the SAME shared diff test-integrity just consumed and asks one question: did
    the change stay inside what its plan declared?

    Self-skipping by design. An increment that
    claimed no scoped `[P]` task has no contract, so the gate is
    *not consulted* — it produces no verdict to ship on and leaves every ordinary
    increment byte-unaffected. When it IS consulted it fails CLOSED: a violation
    blocks, and so does a check that cannot decide (a crash is not an approval,
    #186). The parser is total precisely so that second branch stays unreachable.

    Placed after integrity and before the cognition gates: it costs one string
    scan, so running it early means a hermeticity violation short-circuits ahead
    of every ``claude`` call.
    """

    gate_id: str = "scope"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        # The shared span is MATERIALIZED (spec 013), so it already contains the
        # files the agent chose not to record. This gate held its own
        # `git status --untracked-files=all` probe for exactly that gap; the gap
        # is closed upstream now, for scoped and unscoped increments alike, so
        # the probe is gone. A gate that recomputed the change would be the
        # third component owning its definition — the defect, not a fix.
        diff = await gi.diff()
        try:
            check = scope_check(diff)
        except Exception as err:  # noqa: BLE001 — unreviewable ⇒ fail closed (#186)
            return GateVerdict.failed(
                self.gate_id,
                f"declared-scope check could not produce a verdict "
                f"({err.__class__.__name__}: {err}) — failing closed: an "
                f"unreviewable hermeticity check is not an approval",
            )
        if not check.consulted or not check.violations:
            return GateVerdict.passed(self.gate_id)
        return GateVerdict.failed(self.gate_id, violation_summary(check))


@dataclass
class _ReviewGate:
    """Adversarial pre-PR review over the shared diff (runs only after integrity
    passed, via the short-circuit). Dial-able (ADR 0007): a surviving finding can
    advise-and-ship under `trust`. A review CRASH also surfaces here as a non-ok
    verdict — the queue's fast-fail marker routing (Axis 3) handles it downstream,
    unchanged."""

    queue: "SettleMixin"
    gate_id: str = "review"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        diff = await gi.diff()
        failure = await self.queue._review_failure(
            cast("TaskKind", gi.kind), gi.goal, diff, gi.workspace_dir, scaffold=gi.scaffold,
            project_id=gi.project_id,
        )
        if failure is not None:
            return GateVerdict.failed(self.gate_id, failure, dialable=True)
        return GateVerdict.passed(self.gate_id)


@dataclass
class _BrowserGate:
    """Browser-E2E gate over the shared diff (runs only after integrity + review
    passed). The reasoned reachability escape valve stays INSIDE this gate: a
    would-be block is cleared ONLY when the independent grounded judge affirms the
    changed UI is not rendered in the running app. Dial-able (ADR 0007)."""

    queue: "SettleMixin"
    gate_id: str = "browser"

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        diff = await gi.diff()
        failure = _browser_gate_failure(
            gi.verify, diff, gi.workspace_dir, mode=gi.browser_mode,
            surface=gi.surface,
        )
        if failure is not None and await self.queue._browser_reachability_clears(
            gi.verify, diff, gi.workspace_dir
        ):
            failure = None
        if failure is not None:
            return GateVerdict.failed(self.gate_id, failure, dialable=True)
        return GateVerdict.passed(self.gate_id)
