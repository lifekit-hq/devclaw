"""The direction evaluator — "is this goal going the right way?".

This is the layer the old shipped-PRs-vs-backlog "done" check could not be. That
check was shallow: a PR can be gate-green but wrong; the backlog can drift from
the real intent; *done* is not the same as *good*. Now that the evaluator (devclaw)
sits right next to the repo and the execution context, it judges direction from
GROUNDED ARTIFACTS — the agent's own output, the verify-gate verdicts, the PRs,
and (at the done-gate) a read-only review of the actual repo against done_when —
not from counting backlog items.

It runs as a SEPARATE, less-frequent cognition step from the next-action planner
(the mechanism/cognition split applied to evaluation itself): the cheap per-tick
progress check and per-delivery evidence capture cost ~0 tokens and gate when the
evaluator runs, so direction is judged periodically and at the moment of closing,
never on every tick.

The verdict drives the loop, it doesn't just report:
- ``achieved``    → the goal may close ``done`` (only path to done — the planner's
                    "done" is merely a proposal).
- ``off_track``   → ``corrections`` are written to inbox.md as steering; the
                    next-action planner picks them up and the goal keeps going.
- ``stalled``     → block + notify (thrash / repeated failure that won't self-fix).
- ``needs_human`` → block + notify with a specific question.
- ``on_track``    → record and continue.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable, Optional, cast

from .. import config as _config
from .clause_pin import PinnedClause
from .models import ClauseVerdict, EvalResult, EvalVerdict, Goal, GoalStatus, Strictness, is_standing
from .prompt_budget import cap_deliveries, cap_log

# The review gate's workspace-snapshot collector (#227), reused to ground the
# evaluator. Imported as a module global so tests patch it on THIS module —
# same convention as task_queue's re-export of the git ``_sync`` helpers.
from ..task_git import _review_repo_context_sync  # noqa: F401

ClaudeCaller = Callable[[str], Awaitable[str]]

_VALID_VERDICTS = {"on_track", "off_track", "achieved", "stalled", "needs_human"}

#: the evaluator's model tier. Judging delivered work against intent is more
#: load-bearing than picking the next step → defaults a notch up is reasonable,
#: but sonnet is the cost-conscious default; bump to opus per goal via env.
from ..model_tiers import model_for as _model_for
GOAL_EVAL_MODEL = _model_for("goal_eval")


#: The done-gate de-fat (structural-root-2026-08-05). At the done-gate the
#: decision is "does the repo's END STATE satisfy done_when?" — answered from the
#: fresh read-only repo review + the repository-context snapshot, NOT from the
#: goal's re-fed diary. That diary — the ``## Recent event log`` (cap 24K) + the
#: ``## What has actually shipped`` deliveries record (cap 24K) — is ~half of the
#: 105K done-gate prompt that OOMs/times out (`input_chars=105182` in the live
#: catalog), and it is a stateless-control-plane reflex: the journey and the
#: agent's own delivery CLAIMS are not evidence a clause is met (the prompt itself
#: already ranks them "secondary … claims do not count"). When ON, the done-gate
#: prompt omits both. Default OFF — flip per box after the prompt-anatomy view
#: (#467) confirms the diary is dead weight (mirrors the flag-gated planner cut,
#: #463). Dropping only *claim/history* context can never loosen the gate — the
#: repo review is still required for every clause — so this fails toward CLOSED.
DONEGATE_LEAN = _config.DONEGATE_LEAN


class GoalEvalError(Exception):
    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


async def _repo_context(workspace_dir: str) -> str:
    """A small grounded snapshot of the goal's ACTUAL workspace — remote,
    branch, head, key-file presence, tracked top-level layout — for the
    evaluator prompt. Without it the evaluator has zero first-hand repo facts
    on the artifact-only done path and on mid-flight/on-demand evals, and can
    substitute the control-plane repo host-side ``claude`` was launched from
    (the review-gate sibling of this bug shipped as #227).

    Same async-wrapper convention as ``task_queue._git_diff``: the blocking
    collector runs in a thread, and ``_review_repo_context_sync`` is looked up
    as a module global so tests patch it here. Strictly best-effort — returns
    ``''`` on any hiccup and NEVER raises: grounding is a bonus; a git wobble
    must not fail an evaluation (or the done-gate riding on it)."""
    if not workspace_dir:
        return ""
    try:
        return await asyncio.to_thread(_review_repo_context_sync, workspace_dir)
    except Exception:  # noqa: BLE001 — best-effort by contract (see docstring)
        return ""


#: Headroom for the review report inside the evaluator prompt. Modern runner
#: results carry the agent's final message — the report itself. HISTORICAL
#: task rows on disk instead hold the SDK's full captured-stdout transcript
#: (banner, prompt echo, tool panels; 60–160 KB) with the filled report at the
#: END. ``_extract_review_report`` copes with both shapes.
_REVIEW_REPORT_KEEP = 20000


def _extract_review_report(raw: str) -> str:
    """Pull the agent's actual per-clause report out of the worker's captured
    stdout. The brief mandates a ``## Per-clause evidence`` section followed by
    ``## Summary`` and ``## Risks not in done_when`` — the LAST occurrence of
    that header is the filled-in report (an earlier occurrence, if present, is
    the prompt's own format template echoed back in the SDK's user-message
    panel). When the header isn't present (truly cut-off run), fall back to the
    tail — the tail still preserves any partial work, while the head was
    always just banner + tool-call decoration.

    Centralized here, not in the runner, because (a) all historical task rows
    on disk hold the un-cleaned ``agent_output`` and the done-gate must still
    read them correctly, and (b) the parsing is purely defensive — even a
    future cleaner runner can only emit a best-effort extraction; the
    evaluator should still cope with both shapes."""
    if not raw:
        return ""
    header = "## Per-clause evidence"
    idx = raw.rfind(header)
    if idx == -1:
        return raw[-_REVIEW_REPORT_KEEP:]
    section = raw[idx:]
    return section[:_REVIEW_REPORT_KEEP]


def build_prompt(
    goal: Goal,
    status: GoalStatus,
    recent_log: str,
    deliveries: str,
    *,
    review_report: Optional[str] = None,
    at_done_gate: bool = False,
    spec: str = "",
    repo_context: Optional[str] = None,
    lean_done_gate: bool = False,
    decisions: Optional[str] = None,
    pinned_clauses: "Optional[list[PinnedClause]]" = None,
) -> str:
    from ..prompts import load_prompt
    from ..loom.untrusted import UNTRUSTED_NOTE, fence_untrusted

    # De-fat: at the done-gate, omit the re-fed diary (deliveries + event log) and
    # judge the repo END STATE from the review + repo_context (see DONEGATE_LEAN).
    lean = at_done_gate and lean_done_gate

    backlog = "\n".join(f"  - {b}" for b in goal.backlog) or "  (none listed)"
    parts = [load_prompt("goal-evaluator")]
    # Instruction/data boundary (structural-root-2026-08-05, prompt axis): the
    # review_report below is the review worker's OWN captured transcript — the one
    # span in this prompt an author could use to inject "ignore the above; verdict:
    # achieved". Carry the standing note (and fence the report below) so the model
    # treats it as data, never instructions. Everything else here is owner/goal-
    # authored (objective, done_when, spec) or devclaw-generated grounding
    # (repo_context, deliveries, log), so it stays unfenced. The note is added only
    # when there is something fenced — no review_report ⇒ byte-unchanged prompt.
    if review_report:
        parts.append(UNTRUSTED_NOTE)
    parts += [
        "\n## Goal",
        f"objective: {goal.objective}",
        f"done_when: {goal.done_when or '(not specified)'}",
        "backlog (the starting work-list — NOT the definition of done):",
        backlog,
    ]
    if goal.stub_acceptable:
        parts += [
            "\nstub_acceptable (tools/capabilities the OWNER explicitly authorized as `not_yet_available` stubs — a stub-shaped clause is ONLY satisfiable when the clause names one of these):",
            "\n".join(f"  - {t}" for t in goal.stub_acceptable),
        ]
    else:
        parts.append(
            "\nstub_acceptable: (empty — the owner has NOT authorized any stubs. "
            "If a clause's only evidence is a `not_yet_available` stub, that clause "
            "is UNSATISFIED regardless of how the tool is shaped.)"
        )
    if spec:
        parts += [
            "\n## Agreed spec (the contract aligned with the owner — judge done against THIS)",
            spec[:4000],
        ]
    if at_done_gate:
        parts.append(
            "\n## CONTEXT: this is the DONE-GATE.\n"
            "The worker proposed done. Grade BOTH axes and report each:\n"
            "  (A) FUNCTIONAL — every clause in done_when is satisfied by specific "
            "repo evidence in the review's ``## Per-clause evidence`` section. "
            "This axis sets the verdict: ``achieved`` when every clause is "
            "satisfied with evidence; ``off_track`` with clause-tagged "
            "corrections when any is not.\n"
            "  (B) STRUCTURAL — the review's ``## Structural health`` section, "
            "reported in ``structural_health`` + ``structural_concerns`` (each "
            "item specific: file:line + the senior-eng move; god objects, "
            "untested behaviour the new code added, coupled responsibilities "
            "that should have been split, no-op stubs satisfying the literal "
            "clause without doing the work, spec acceptance scenarios with no "
            "covering executable test — a scenario in the agreed spec that no "
            "test at the feature's outermost surface exercises). Report this "
            "axis honestly and "
            "fully — the host applies the goal's strictness dial to it — but "
            "it never sets the verdict and its items never go in "
            "``corrections``. The literal-clauses-pass-but-codebase-is-worse "
            "failure mode (closeloop's App.tsx grew to 1827 LOC through 4 PRs "
            "that each verdicted ``achieved`` on the functional axis) is "
            "exactly what this axis exists to catch."
        )
        if is_standing(goal.done_when):
            parts.append(
                "\n## STANDING-GOAL CONTRACT\n"
                "This goal's done_when disclaims boundedness (it declares "
                "itself a STANDING goal with no terminal completion state). "
                "You must NOT return ``achieved`` — a standing goal is closed "
                "by the owner, never by this gate. If any clause or the "
                "structural axis fails, return ``off_track`` with corrections "
                "as usual. If everything currently graded passes, return "
                "``needs_human`` with a question telling the owner the work "
                "in flight looks complete and asking them to either close the "
                "goal themselves or steer the next direction. (The mechanical "
                "validator converts a stray ``achieved`` to ``needs_human`` — "
                "don't invite it.)"
            )
    if at_done_gate and pinned_clauses:
        # Spec 035: the rubric of record. This contract revision was
        # decomposed once (the pin); the model judges exactly that list —
        # evidence fresh each round, the rubric never re-derived. Prior
        # satisfied state is shown so the flip rule (FR-011) is judgeable:
        # un-satisfying a ✓ clause requires a cited cause.
        listing = "\n".join(
            f"  - [{c.id}]"
            + (f" ✓ satisfied (round {c.satisfied_round})" if c.satisfied else "")
            + f" {c.text}"
            for c in pinned_clauses
        )
        parts.append(
            "\n## Pinned clauses (the decomposition of record for this "
            "contract revision)\n"
            "Skip procedure steps 1/1a — the decomposition already happened "
            "and is fixed. Judge EXACTLY the clauses below: never add, "
            "remove, merge, split, or rename one, and do not emit "
            "`dropped_ceremony`. Every entry in your `clauses` array MUST "
            "carry its pinned `id` (e.g. \"id\": \"c1\"), and every pinned id "
            "MUST appear exactly once. The pinned text is authoritative — "
            "gather fresh evidence for each clause as usual. A clause marked "
            "✓ was satisfied with cited evidence in an earlier round: to mark "
            "it unsatisfied now, its entry MUST also carry \"flip_cause\" — "
            "the repo change since that evidence, or the specific defect in "
            "it; never flip without one. Tag each correction with its pinned "
            "id (e.g. \"[c2] add the test\").\n"
            + listing
        )
    if repo_context and repo_context.strip():
        parts += [
            "\n## Repository context (facts from the actual workspace — the "
            "source of truth for repo identity and which files exist)",
            repo_context.strip(),
        ]
    if decisions and decisions.strip():
        # spec 031 US4: the owner's recorded rulings. A clause with a current
        # Decision is graded resolved_by_decision — settled, never re-asked.
        parts += [
            "\n## Decisions (the owner's recorded rulings on this goal — authoritative)",
            decisions.strip(),
        ]
    if lean:
        # The done decision is about the repo's END STATE, not the journey: the
        # delivery record and event log (the two 24K-capped diary blocks that
        # dominate the prompt) are omitted here. The prompt's own rules already
        # rank them "secondary … claims do not count" — so this drops non-evidence,
        # never a clause's grounding, and can only fail toward CLOSED.
        parts.append(
            "\n(The delivery record and event log are intentionally omitted at "
            "this gate. Judge every done_when clause from the fresh repo review "
            "and the repository context above — the agent's delivery claims and "
            "the event history are NOT evidence that a clause is satisfied.)"
        )
    else:
        parts += [
            "\n## What has actually shipped (grounded deliveries)",
            cap_deliveries(deliveries) or "(nothing delivered yet)",
            "\n## Recent event log",
            cap_log(recent_log) or "(no events yet)",
        ]
    if review_report:
        parts += [
            "\n## Fresh read-only review of the current repo vs done_when",
            fence_untrusted(
                "REPO REVIEW REPORT", _extract_review_report(review_report)
            ),
        ]
    parts.append("\nReturn the JSON now.")
    return "\n".join(parts)


def extract_json(text: str) -> str:
    trimmed = text.strip()
    if trimmed.startswith("{"):
        return trimmed
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", trimmed)
    if fence and fence.group(1):
        return fence.group(1)
    first, last = trimmed.find("{"), trimmed.rfind("}")
    if first >= 0 and last > first:
        return trimmed[first : last + 1]
    raise GoalEvalError("No JSON object found in evaluator response", text)


def _parse_clauses(
    raw: object, pinned: "Optional[dict[str, str]]" = None,
    prior_satisfied: "Optional[set[str]]" = None,
) -> list[ClauseVerdict]:
    """Parse the model's ``clauses`` array. Tolerant of shape drift: drops
    entries that aren't dicts, coerces bool-ish ``satisfied`` values
    (true/false, "yes"/"no", "partial" → False).

    ``pinned`` (spec 035, id → verbatim text) switches on the pinned-rubric
    contract: every entry MUST carry a known ``id`` exactly once and every
    pinned id MUST be judged. The pinned text is substituted as the clause
    text — renaming is impossible by construction. Violations raise
    :class:`GoalEvalError` (a malformed verdict fails the round closed and
    is a mechanism failure, never a judgment — FR-002/FR-006).

    ``prior_satisfied`` (spec 035 US2, FR-011): the ids the pin records as
    satisfied-with-evidence. Un-satisfying one requires a non-empty
    ``flip_cause`` — the repo change since that evidence, or its named
    defect — folded into the clause's evidence so every downstream consumer
    (corrections, log, pin accounting) carries the cause; a causeless flip
    is a malformed verdict."""
    if not isinstance(raw, list):
        if pinned:
            raise GoalEvalError("pinned-mode verdict carries no clauses array")
        return []
    seen_ids: set[str] = set()
    out: list[ClauseVerdict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            if pinned:
                raise GoalEvalError("pinned-mode clauses entry is not an object")
            continue
        if pinned is not None:
            cid = str(entry.get("id", "")).strip()
            if cid not in pinned:
                raise GoalEvalError(
                    f"verdict references unknown clause id {cid or '(missing)'!r} — "
                    f"the pinned rubric has only {sorted(pinned)}"
                )
            if cid in seen_ids:
                raise GoalEvalError(f"verdict judges pinned clause id {cid!r} twice")
            seen_ids.add(cid)
            clause = pinned[cid]
        else:
            clause = str(entry.get("clause", "")).strip()
            if not clause:
                continue
        sat_raw = entry.get("satisfied")
        if isinstance(sat_raw, bool):
            satisfied = sat_raw
        elif isinstance(sat_raw, str):
            # "yes" → True; "partial" / "no" / anything else → False (the strict
            # contract: partial doesn't satisfy a clause at the done-gate)
            satisfied = sat_raw.strip().lower() == "yes"
        else:
            satisfied = False
        evidence = str(entry.get("evidence", "")).strip()
        # spec 031 US4: a clause the model grades resolved_by_decision carries
        # the Decision id; it counts as satisfied with that id as evidence. A
        # non-string or empty value is ignored (an undocumented output field
        # is never honoured, #233) and the clause is graded as today.
        rb = entry.get("resolved_by")
        resolved_by = rb.strip()[:64] if isinstance(rb, str) else ""
        if resolved_by:
            satisfied = True
            evidence = evidence or f"resolved by decision {resolved_by}"
        # FR-011 (spec 035 US2): un-satisfying a clause the pin records as
        # satisfied requires a cited cause — a repo change since the
        # satisfying evidence, or its named defect. The cause rides in the
        # evidence so corrections, the goal log, and the pin accounting all
        # carry it. Causeless flip ⇒ malformed round (fails closed, no churn
        # charge). Free flips were half the fs-479 incident: satisfied at
        # 11:06, the sole failure at 12:24, no repo change in between.
        if (
            pinned is not None and prior_satisfied and not satisfied
            and cid in prior_satisfied
        ):
            fc = entry.get("flip_cause")
            flip_cause = fc.strip() if isinstance(fc, str) else ""
            if not flip_cause:
                raise GoalEvalError(
                    f"verdict flips previously-satisfied clause {cid!r} to "
                    f"unsatisfied without a flip_cause — cite the repo change "
                    f"since its evidence, or the specific defect in it"
                )
            evidence = (f"{evidence} " if evidence else "") + f"[flip cause: {flip_cause}]"
        out.append(ClauseVerdict(
            clause=clause, satisfied=satisfied, evidence=evidence, resolved_by=resolved_by,
        ))
    if pinned is not None:
        missing = set(pinned) - seen_ids
        if missing:
            raise GoalEvalError(
                f"verdict omitted pinned clause id(s) {sorted(missing)} — "
                f"every pinned clause must be judged each round"
            )
    return out


#: case-insensitive substrings that mean "this clause is being satisfied by a
#: stub, not by real work." The mechanical check below flips a satisfied clause
#: to unsatisfied when one of these is present in clause+evidence AND the
#: owner did not authorize a stub for the named tool.
_STUB_MARKERS = ("not_yet_available", "notyetavailable", "legit_stub")


def _looks_like_stub(text: str) -> bool:
    s = text.lower()
    return any(m in s for m in _STUB_MARKERS)


#: The closeloop-bench-2026-07-05 failure mode this net closes: the goal's
#: verify.sh asserted that the Playwright spec files EXISTED (a grep-shaped
#: check()), never executed them, and the done-gate stamped the test clause
#: green. Existence is not execution: a test-shaped clause whose evidence
#: speaks of file presence with no run marker is flipped to unsatisfied.
_TEST_CLAUSE_RE = re.compile(
    r"\btest(?:s|ed|ing)?\b|\bcoverage\b|\be2e\b", re.IGNORECASE,
)
# ``present`` is only existence wording when it describes a FILE. Bare
# ``present`` also describes DATA — "asserts the counts row is absent when
# `_errors` present" is a behavioral assertion, not a presence check — and
# matching it flipped a satisfied clause on lkd-honest-widgets-2026-08-21
# (2026-08-21), holding a fully-met contract open. Error-state testing uses
# "<field> present" constantly, so the collision is systematic, not a one-off.
# The file-ish alternatives below keep every real presence phrasing.
_EXISTENCE_EVIDENCE_RE = re.compile(
    r"\bexists?\b|\bexistence\b|\bchecked[- ]in\b"
    r"|\b(?:file|files|spec|specs|suite|module)s?\s+(?:is\s+|are\s+)?present\b"
    r"|\bpresent\s+(?:in|at)\s+(?:the\s+)?(?:repo|repository|tree|codebase|directory)\b",
    re.IGNORECASE,
)
_EXECUTION_EVIDENCE_RE = re.compile(
    r"\bpass(?:es|ed|ing)?\b|\bran\b|\bruns?\b|\bexecut(?:ed|es|ion)\b"
    r"|\bexit (?:code )?0\b|\bgreen\b|\b\d+\s+(?:tests?|specs?|cases?)\b",
    re.IGNORECASE,
)


def _test_clause_existence_only(clause: ClauseVerdict) -> bool:
    """True when a test-shaped clause is being satisfied by evidence that
    proves the test files are PRESENT but never says they EXECUTED. Deliberately
    conservative: both conditions must hold (existence wording present AND no
    execution wording), so "HealthTests.cs:8 Health_Returns200 passes" and
    "suite green in verify.sh" are untouched."""
    if not _TEST_CLAUSE_RE.search(clause.clause):
        return False
    ev = clause.evidence
    return bool(_EXISTENCE_EVIDENCE_RE.search(ev)) and not _EXECUTION_EVIDENCE_RE.search(ev)


#: The host-written verify-gate marker inside a delivery body
#: (``engine._task_detail``) — the same devclaw-controlled line
#: ``prior_increments._GATE_RE`` feeds forward, and the same trust boundary:
#: the worker's free-text ``Agent summary:`` shares the body, so this reads a
#: host-authored fact with the accepted residual risk of a worker echoing the
#: exact line shape at column 0.
_VERIFY_GATE_RE = re.compile(r"^Verify gate\s+`[^`]*`:\s*(PASSED|FAILED)", re.MULTILINE)


def _deliveries_verified_execution(deliveries: str) -> bool:
    """Whether the deliveries tail carries mechanical run evidence: the LAST
    host-written ``Verify gate …:`` marker is PASSED. Last wins — an old green
    increment must never vouch for a newer failed one. No marker ⇒ False."""
    matches = _VERIFY_GATE_RE.findall(deliveries or "")
    return bool(matches) and matches[-1] == "PASSED"


_VERB_PREFIXES = ("get", "list", "fetch", "read", "describe", "show")


def _norm(s: str) -> str:
    """Aggressive identifier normalization for cross-naming-convention match:
    lowercase, strip ``_ - `` and whitespace. Lets ``get_cashflow_report`` find
    its evidence in ``CashflowReportStub.cs`` (different naming convention,
    same underlying capability)."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _slug_variants(name: str) -> list[str]:
    """For a stub_acceptable entry, return the normalized forms we'll search
    for in the clause/evidence. Includes the verb-stripped variant so an MCP
    tool slug like ``get_cashflow_report`` matches a C# evidence string like
    ``CashflowReportStub.cs`` (which has no ``get`` prefix)."""
    n = _norm(name)
    if not n:
        return []
    variants = [n]
    for prefix in _VERB_PREFIXES:
        if n.startswith(prefix) and len(n) > len(prefix) + 2:
            variants.append(n[len(prefix):])
            break
    return variants


def _stub_is_authorized(clause: ClauseVerdict, stub_acceptable: list[str]) -> bool:
    """A stub-shaped clause is authorized when the owner's ``stub_acceptable``
    list names a tool/capability that appears in the clause text or its
    evidence. Match is case-insensitive AND naming-convention-insensitive:
    tool slug ``get_cashflow_report`` authorizes evidence mentioning
    ``CashflowReportStub.cs`` or ``cashflow report`` or any other casing the
    repo/model uses."""
    if not stub_acceptable:
        return False
    haystack = _norm(f"{clause.clause}\n{clause.evidence}")
    for name in stub_acceptable:
        for variant in _slug_variants(name):
            if variant in haystack:
                return True
    return False


#: values the LLM may return for ``structural_health`` at the done-gate. Any
#: other string is treated as "" (unknown → we don't downgrade on missing data;
#: the clause-level enforcement still runs).
_VALID_STRUCTURAL = {"clean", "concerns", "poor"}


def _parse_structural(parsed: dict) -> tuple[str, list[str]]:
    """Extract ``structural_health`` + ``structural_concerns`` from the model
    response. Tolerates missing / mistyped fields — ``structural_health`` maps
    only known values through, empty otherwise, so the mechanical downgrade
    can safely no-op on non-done-gate responses."""
    raw_h = parsed.get("structural_health")
    health = str(raw_h).strip().lower() if raw_h else ""
    if health not in _VALID_STRUCTURAL:
        health = ""
    raw_c = parsed.get("structural_concerns") or []
    concerns: list[str] = []
    if isinstance(raw_c, list):
        for c in raw_c:
            s = str(c).strip()
            if s:
                concerns.append(s)
    return health, concerns


def _downgrade_rationale(reason: str, model_rationale: str) -> str:
    """The rationale recorded when a mechanical post-check downgrades an
    ``achieved`` verdict. The mechanical reason leads — the model's original
    rationale argued FOR the close, so left standing alone next to
    ``off_track`` it makes the log contradict itself (the
    devclaw-auth-ping-path-2026-08-25 round-1 record read "off_track — All
    four clauses have repo-confirmed evidence…"). The model text stays as
    trailing context."""
    if model_rationale:
        return f"downgraded from 'achieved': {reason} (model rationale: {model_rationale})"
    return f"downgraded from 'achieved': {reason}"


def validate(
    parsed: object,
    *,
    at_done_gate: bool = False,
    stub_acceptable: list[str] | None = None,
    standing: bool = False,
    strictness: Strictness = "strict",
    verified_execution: bool = False,
    pinned_clauses: "Optional[list[PinnedClause]]" = None,
) -> EvalResult:
    """Validate + normalize the model's evaluation. When ``at_done_gate=True``,
    ``achieved`` requires (a) every clause in ``clauses`` to be ``satisfied=True``
    with non-empty ``evidence`` AND (b) ``structural_health`` in {``clean``,
    ``concerns``-with-no-substantive-items}. Otherwise the verdict is downgraded
    to ``off_track`` — the safety net that closes both the 2026-06-25 "stub
    everything" failure mode (axis A) AND the closeloop-D1/D2/D6 monolith
    creep (axis B, per plan.md §Production-ready C3).

    ``standing=True`` (the goal's done_when disclaims boundedness) adds a third
    mechanical net at the done-gate: a fully-passing ``achieved`` becomes
    ``needs_human`` — the owner closes standing goals, never the gate. The
    closeloop-bench-2026-07-05 contract said "standing goal — not a bounded
    criterion" and still terminally closed ``achieved``; this is the fix.

    ``verified_execution=True`` (the caller holds mechanical run evidence — the
    deliveries tail's last host-written verify-gate marker is PASSED) disarms
    the existence-only test-clause flip: a fact the host established by running
    the suite outranks how the model happened to word its evidence."""
    if not isinstance(parsed, dict):
        raise GoalEvalError("Eval must be a JSON object")
    raw_verdict = parsed.get("verdict")
    if raw_verdict not in _VALID_VERDICTS:
        raise GoalEvalError(f"verdict must be one of {_VALID_VERDICTS}, got {raw_verdict!r}")
    verdict: EvalVerdict = cast(EvalVerdict, raw_verdict)
    rationale = str(parsed.get("rationale", "")).strip()
    raw_corr = parsed.get("corrections") or []
    corrections = [str(c).strip() for c in raw_corr if str(c).strip()] if isinstance(raw_corr, list) else []
    question = str(parsed.get("question", "")).strip()
    # Spec 035: in pinned mode the id contract is enforced here — an unknown,
    # duplicate, or omitted pinned id, or a causeless flip of a satisfied
    # clause (FR-011), raises out of this call (the round fails closed as a
    # mechanism failure, never a judgment).
    pinned_map = {c.id: c.text for c in pinned_clauses} if pinned_clauses else None
    prior_satisfied = (
        {c.id for c in pinned_clauses if c.satisfied} if pinned_clauses else None
    )
    clauses = _parse_clauses(parsed.get("clauses"), pinned_map, prior_satisfied)
    structural_health, structural_concerns = _parse_structural(parsed)
    if verdict == "needs_human" and not question:
        # tolerate a model that put the ask in rationale rather than question
        question = rationale or "the evaluator needs a human decision (no question given)"
    if at_done_gate and verdict == "off_track" and clauses:
        unsatisfied = [c for c in clauses if not c.satisfied or not c.evidence]
        if not unsatisfied:
            # A met contract typed off_track: every clause satisfied with
            # evidence, so whatever sits in ``corrections`` is improvement the
            # contract does not require (a DI preference, a parameter default,
            # a "senior move") — the structural axis, not contract work.
            # Demote it there and route through the achieved checks below: the
            # stub/existence normalization and the structural dial apply the
            # SAME close bar either way, so the verdict label alone cannot
            # hold a met contract open on taste.
            if corrections:
                structural_concerns = list(structural_concerns) + corrections
                if structural_health not in ("poor", "concerns"):
                    structural_health = "concerns"
                corrections = []
            verdict = "achieved"
        elif not corrections:
            # Contract work remains but the model named no fix — derive the
            # steering from the unsatisfied clauses themselves so the next
            # advance brief is never byte-identical to the last.
            corrections = [
                f"[clause: {c.clause}] {c.evidence or 'no evidence provided'} — "
                f"address this before declaring done."
                for c in unsatisfied
            ]
    if verdict == "off_track" and not corrections and structural_concerns:
        # Steering must never land empty on an actionable verdict (an empty
        # corrections list re-dispatches a byte-identical brief — a blind
        # loop). With no clause grading to derive from, the structural items
        # are the only steering available.
        corrections = [f"[structural] {c}" for c in structural_concerns]
    if verdict == "off_track" and not corrections and not structural_concerns:
        # off_track is only actionable with corrections; treat a bare off_track as
        # a soft on_track so we don't silently stall without steering.
        return EvalResult(
            verdict="on_track", rationale=rationale or "no corrections given",
            clauses=clauses, structural_health=structural_health,
            structural_concerns=structural_concerns,
        )
    # Done-gate strictness: achieved MUST be backed by per-clause evidence; the
    # model can technically still claim achieved with no clauses (or with some
    # unsatisfied), so we re-check here and downgrade with derived corrections.
    if at_done_gate and verdict == "achieved":
        if not clauses:
            return EvalResult(
                verdict="off_track",
                rationale=_downgrade_rationale(
                    "evaluator returned 'achieved' but provided no per-clause "
                    "evidence — the done-gate requires explicit clause-by-clause "
                    "grading.",
                    rationale,
                ),
                corrections=[
                    "Return a per-clause `clauses` array with satisfied + evidence for "
                    "every atomic done_when requirement; do not claim 'achieved' without "
                    "it."
                ],
                clauses=clauses,
                structural_health=structural_health,
                structural_concerns=structural_concerns,
            )
        # Mechanical stub-policy enforcement: a satisfied clause whose evidence
        # is structurally a stub (not_yet_available payload, *Stub class, etc.)
        # is only allowed when the owner's stub_acceptable lists the tool the
        # clause refers to. Otherwise we flip it to unsatisfied — the safety
        # net for the 2026-06-26 v5 failure mode where the agent shipped four
        # stubs as "done" and the gate stamped them green.
        allowed_stubs = list(stub_acceptable or [])
        normalized: list[ClauseVerdict] = []
        for c in clauses:
            if c.satisfied and c.evidence and _looks_like_stub(f"{c.clause}\n{c.evidence}"):
                if not _stub_is_authorized(c, allowed_stubs):
                    normalized.append(ClauseVerdict(
                        clause=c.clause, satisfied=False,
                        evidence=(
                            f"unauthorized stub — evidence ({c.evidence!s}) is a "
                            f"not_yet_available stub but the goal's stub_acceptable "
                            f"does not list this tool. Either implement the real "
                            f"capability or add the tool name to stub_acceptable in "
                            f"goal.yaml to explicitly accept the stub."
                        ),
                    ))
                    continue
            # Execution-evidence enforcement for test clauses: presence of the
            # spec files is not coverage. The flip message names what WOULD
            # satisfy the clause so the correction steers the next action.
            if (
                c.satisfied and c.evidence and _test_clause_existence_only(c)
                and not verified_execution
            ):
                normalized.append(ClauseVerdict(
                    clause=c.clause, satisfied=False,
                    evidence=(
                        f"existence-only test evidence — ({c.evidence!s}) proves "
                        f"the test files are PRESENT, not that they EXECUTED and "
                        f"passed. A test clause needs run evidence: the verify "
                        f"gate's run output, a test count, or the passing suite "
                        f"log. An existence grep does not satisfy a test clause."
                    ),
                ))
                continue
            normalized.append(c)
        clauses = normalized
        unsatisfied = [c for c in clauses if not c.satisfied or not c.evidence]
        if unsatisfied:
            derived = [
                f"[clause: {c.clause}] {c.evidence or 'no evidence provided'} — "
                f"address this before declaring done."
                for c in unsatisfied
            ]
            return EvalResult(
                verdict="off_track",
                rationale=_downgrade_rationale(
                    f"{len(unsatisfied)} of {len(clauses)} done_when clause(s) "
                    "lack confirmed evidence after mechanical normalization.",
                    rationale,
                ),
                corrections=derived,
                clauses=clauses,
                structural_health=structural_health,
                structural_concerns=structural_concerns,
            )
        # Functional axis passes — now the structural axis (C3, plan.md
        # §Production-ready), which obeys the goal's strictness dial (ADR
        # 0007): under ``strict`` a reported concern holds the close open
        # (the downgrade below); under ``trust`` it advises-and-ships — the
        # concerns ride the close as loud follow-ups (surfaced by the
        # done-gate close path; the human PR review is the backstop) and do
        # NOT hold a met contract open. Missing structural_health is treated
        # as unknown (no flip) — such responses stay observable rather than
        # silently failing; the prompt mandates the field.
        structural_fail = (
            structural_health == "poor"
            or (structural_health == "concerns" and structural_concerns)
        )
        if structural_fail and strictness == "strict":
            derived = [
                f"[structural: {structural_health}] {c}"
                for c in (structural_concerns or ["structural review reported "
                          "'poor' but named no concerns — resurface the "
                          "review's ## Structural health block and cite the "
                          "specific file:line + fix each item."])
            ]
            return EvalResult(
                verdict="off_track",
                rationale=_downgrade_rationale(
                    f"functional clauses met but structural axis failed "
                    f"({structural_health}); clean up the shape before "
                    "declaring done.",
                    rationale,
                ),
                corrections=derived,
                clauses=clauses,
                structural_health=structural_health,
                structural_concerns=structural_concerns,
            )
        # Standing-goal contract: both axes pass, but the owner declared this
        # goal unbounded — the gate hands the close decision over instead of
        # taking it. needs_human blocks the goal + notifies, which is exactly
        # the shape "everything you asked for is in flight; close or re-aim"
        # should have.
        if standing:
            return EvalResult(
                verdict="needs_human",
                rationale=rationale,
                question=(
                    "standing-goal contract: every currently-graded done_when "
                    "axis passes, but this goal's done_when declares it "
                    "STANDING — the done-gate does not terminally close it. "
                    "Close it yourself (cancel_goal) if the mission is "
                    "complete, or steer the next direction."
                ),
                clauses=clauses,
                structural_health=structural_health,
                structural_concerns=structural_concerns,
            )
    return EvalResult(
        verdict=verdict, rationale=rationale, corrections=corrections,
        question=question, clauses=clauses,
        structural_health=structural_health,
        structural_concerns=structural_concerns,
    )


async def evaluate(
    goal: Goal,
    status: GoalStatus,
    recent_log: str,
    deliveries: str,
    *,
    claude_caller: ClaudeCaller,
    review_report: Optional[str] = None,
    at_done_gate: bool = False,
    spec: str = "",
    repo_context: Optional[str] = None,
    lean_done_gate: Optional[bool] = None,
    strictness: Optional[Strictness] = None,
    decisions: Optional[str] = None,
    pinned_clauses: "Optional[list[PinnedClause]]" = None,
) -> EvalResult:
    """Run the direction evaluation. ``claude_caller`` is injected so tests stub
    the LLM. Pass ``review_report`` + ``at_done_gate`` when judging a done proposal;
    ``spec`` (the waiter-provided scope contract) when one exists, so done is
    judged against it; ``repo_context`` (the :func:`_repo_context` workspace
    snapshot) so repo identity is first-hand, never inferred. ``lean_done_gate``
    (default = the :data:`DONEGATE_LEAN` env flag; read at call time so tests can
    monkeypatch it) omits the re-fed diary at the done-gate."""
    lean = DONEGATE_LEAN if lean_done_gate is None else lean_done_gate
    prompt = build_prompt(
        goal, status, recent_log, deliveries,
        review_report=review_report, at_done_gate=at_done_gate, spec=spec,
        repo_context=repo_context, lean_done_gate=lean,
        decisions=decisions, pinned_clauses=pinned_clauses,
    )
    raw = await claude_caller(prompt)
    try:
        parsed = json.loads(extract_json(raw))
    except json.JSONDecodeError as exc:
        raise GoalEvalError(f"evaluator emitted invalid JSON: {exc}", raw) from exc
    # ``strictness`` (spec 016 FR-008): the caller passes the LIVE resolved
    # dial (explicit goal > manifest default) — falling back to the goal's
    # resolved field keeps every pre-016 caller byte-identical.
    result = validate(
        parsed, at_done_gate=at_done_gate, stub_acceptable=goal.stub_acceptable,
        standing=is_standing(goal.done_when),
        strictness=strictness or goal.strictness,
        verified_execution=_deliveries_verified_execution(deliveries),
        pinned_clauses=pinned_clauses,
    )
    # Spec 035 pin harvest: the ceremony spans step 1a dropped, recorded once
    # with the pin. Only meaningful on a decomposition-mode done-gate round;
    # a pinned round was told not to emit the field, and a stray emission is
    # ignored (an undocumented-for-this-mode output field is never honoured).
    if at_done_gate and not pinned_clauses:
        raw_drops = parsed.get("dropped_ceremony") or []
        if isinstance(raw_drops, list):
            drops = [str(d).strip() for d in raw_drops if str(d).strip()]
            if drops:
                from dataclasses import replace as _dc_replace

                result = _dc_replace(result, dropped_ceremony=drops)
    return result


def default_caller() -> ClaudeCaller:
    """Production cognition caller bound to the evaluator tier (lazy import)."""
    from ..llm_call import claude_with_model

    return claude_with_model(GOAL_EVAL_MODEL, role="evaluator")
