"""Goal-layer domain types — the durable mind, as plain data.

Folded in from goalclaw. A :class:`Goal` is the durable objective (read from
``goal.yaml``); a :class:`GoalStatus` is the mutable point-in-time state
(``STATUS.md`` frontmatter), overwritten each tick. An :class:`Action` is a
single engine call the tick dispatches. :class:`EvalResult` is the direction evaluator's verdict —
the layer that asks "is this going the right way?" not just "did it ship?".

These are deliberately separate from the task types in
``state_store.py``: a ``task`` is one bounded engine run; a ``goal`` is an
open-ended standing intent advanced across days via
the heartbeat + steering. Different time-scales, different lifecycles — the goal
layer sits *above* the task engine and dispatches into it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

# The engine literal stays a Literal for forward-compat (a future content engine
# would extend it), but code is the only engine today and dispatch is in-process.
Engine = Literal["devclaw"]
#: the engine verbs the goal layer can dispatch — a subset of devclaw's task
#: kinds.
GoalTool = Literal[
    "implement_feature", "fix_bug", "review_repository",
    "validate_product",
]
Phase = Literal["idle", "in_flight", "verifying", "blocked", "done", "cancelled"]
#: The goal lifecycle — ``executing`` only since the host-cognition chain was
#: removed (spec 008 shrink: the worker plans via speckit in-sandbox). NOT
#: optional: the #616 cutoff migrated every NULL and every pre-shrink
#: "investigating"/"firming" row to this one value, so there is no second
#: shape left to coerce at read time.
Lifecycle = Literal["executing"]
EvalVerdict = Literal["on_track", "off_track", "achieved", "stalled", "needs_human"]
#: The execution dial (ADR 0003): both modes ride ONE execution path (the
#: speckit advance loop, spec 008); the dial is re-evaluation cadence, never a
#: different execution stack ("done" is still a proposal gated on the grounded
#: done-gate review in both modes).
#: ``qa`` (spec 015 US3) is the "never self-advances" point on the same dial:
#: the goal owns validation runs (deploy-triggered, or an owner-armed cadence)
#: over the SAME execution path — it never plans feature work, never proposes
#: done (its done_when is standing by construction), and idles at zero
#: cognition.
GoalMode = Literal["long_lived", "one_shot", "qa"]

#: The standing done_when a qa goal is created with (matches
#: :func:`is_standing`, so the done-gate could never close it even if opened).
QA_DONE_WHEN = (
    "Standing goal — continuous live validation of the running product; "
    "not a bounded criterion (no terminal completion state). Validation runs "
    "are triggered by completed deploys and by an owner-armed cadence; the "
    "owner closes this goal."
)

#: the gate strictness dial (ADR 0007). ``strict`` = a dial-able gate that fails
#: BLOCKS the goal (today's fail-closed behavior). ``trust`` = a dial-able gate
#: that fails is recorded loud (log + problems catalog + eval_outcomes) and
#: surfaced in the PR body, but the change SHIPS rather than wedging — the human
#: merge on open-PR-only delivery is the strict backstop. Only the two
#: review-shaped gates (browser-E2E, adversarial review) obey the dial; the
#: evidence-integrity gates (test-integrity, delivery-trust, done-gate) stay hard
#: in BOTH modes (they guard against the model gaming its own evidence, which the
#: human merge can't catch). Defaulted to ``trust`` so every existing goal.yaml
#: (which predates the field) loads advisory — the scoreboard is clean-nights,
#: not reliability, so a wedge costs more than a visibly-flagged imperfect diff.
Strictness = Literal["trust", "strict"]


@dataclass(frozen=True)
class Goal:
    """The durable objective. Read from ``<goal_id>/goal.yaml``; treated as facts."""

    id: str
    objective: str
    #: heartbeat cadence to re-plan even with no event, e.g. "6h", "1d"
    cadence: str
    engine: Engine
    workspace_dir: str
    #: git URL of the target repo — the goal layer clones it if workspace_dir is
    #: empty, and resets to its default branch before each action. None → must pre-exist.
    repo_url: Optional[str] = None
    #: gate command devclaw runs after the agent ("the agent's done is not trusted")
    verify_cmd: Optional[str] = None
    #: when True, devclaw delivers each change as a PR to review
    open_pr: bool = True
    #: prose statement of completion, evaluated by the direction evaluator
    done_when: str = ""
    #: concrete starting work-list the planner draws the next action from
    backlog: list[str] = field(default_factory=list)
    #: first-class issue references (spec 019): ordered issue NUMBERS on this
    #: goal's own repository. Non-empty ⇒ the referenced lane — the dispatch
    #: boundary fetches each issue's LIVE state into the worker brief and a
    #: closed issue is dropped from the remaining scope; empty ⇒ the
    #: issue-less lane, today's behavior unchanged. The lane IS this field —
    #: no separate flag to drift.
    issue_refs: list[int] = field(default_factory=list)
    #: explicit list of MCP tool names (or capability slugs) for which a
    #: ``not_yet_available`` stub is an acceptable terminal state. The
    #: decomposer is forbidden from emitting stubs unless the tool appears
    #: here; the done-gate refuses to mark a stub-shaped clause satisfied
    #: unless one of these names appears in the clause/evidence text. Empty
    #: list (the default) means "no stubs allowed — plan real work."
    stub_acceptable: list[str] = field(default_factory=list)
    #: the execution dial (see :data:`GoalMode`). Defaulted so every existing
    #: goal.yaml (which predates the field) loads as today's per-tick loop.
    mode: GoalMode = "long_lived"
    #: the gate strictness dial (see :data:`Strictness`, ADR 0007). Defaulted to
    #: "trust" so existing goal.yaml (predates the field) loads advisory.
    strictness: Strictness = "trust"
    #: the RAW dial (spec 016 FR-008): the value only when the author/operator
    #: explicitly set it (create param or set_strictness); None when the goal
    #: never chose — which is what lets a repo's ``devclaw.json``
    #: strictnessDefault apply (most-specific-wins, resolved live). ``strictness``
    #: above stays the resolved non-null view for pre-016 readers.
    strictness_explicit: Optional[Strictness] = None
    #: ---- the authored saga slots (spec 012 US2, FR-007) -------------------
    #: Three named slots the author fills at creation, alongside ``objective``
    #: ("what is being achieved") and ``done_when`` ("what completion means").
    #: They are re-sent in the framing of EVERY increment (FR-009a), so each one
    #: has to earn its tokens with a reader that acts on it — here, the worker
    #: (FR-009). ``devclaw/goal/saga_framing.py`` is that reader's generator.
    #:
    #: ``None`` vs ``[]`` is LOAD-BEARING and must not be collapsed:
    #:   * ``None``  — the key is absent from goal.yaml, i.e. the goal was
    #:     authored BEFORE the slot schema. The framing omits the section
    #:     entirely, so a live prose-authored goal keeps its exact brief.
    #:   * ``[]``    — the author looked and declared the slot empty. The
    #:     framing states that absence explicitly (same doctrine as FR-004),
    #:     which is what makes two independently-authored sagas structurally
    #:     identical (SC-003).
    #: Admission rejects ``None`` on a NEW goal, naming the slot (FR-008).
    #: what this saga deliberately does NOT include
    out_of_scope: Optional[list[str]] = None
    #: properties that must still hold after every increment
    invariants: Optional[list[str]] = None
    #: settled decisions the worker must use rather than re-derive
    established: Optional[list[str]] = None

    #: the owning project's reference key (#524 P3). The per-project override
    #: knobs (verify_done, autodeploy, sandbox_image) resolve BY this
    #: id, not by a workspace-path scan. None for self-fix goals with no
    #: registered project, and for a goal.yaml written before P3 (until the
    #: one-shot backfill stamps it) — both fall to the devclaw-wide defaults.
    project_id: Optional[str] = None


#: case-insensitive markers by which a ``done_when`` disclaims boundedness —
#: the owner is saying "this goal has no terminal completion state; judge each
#: delivery, don't ever close it". The closeloop-bench-2026-07-05 contract read
#: "Not applicable as a bounded criterion — this is a standing goal" and the
#: done-gate still returned a terminal ``achieved``; :func:`is_standing` is how
#: the evaluator honors that wording instead of overriding it. Deliberately a
#: short, conservative list: a false positive merely routes the close decision
#: to the owner (needs_human), a false negative reproduces the benchmark bug —
#: extend it when a real contract phrasing slips through, not speculatively.
_STANDING_DONE_WHEN = re.compile(
    r"standing goal"
    r"|not a bounded criterion"
    r"|not applicable as a bounded"
    r"|no terminal (?:state|completion)",
    re.IGNORECASE,
)


def is_standing(done_when: str) -> bool:
    """True when ``done_when`` declares the goal standing (unbounded). Such a
    goal must never terminally close via the done-gate — completion is the
    owner's call (``cancel_goal`` / re-aim), so an all-clauses-pass done-gate
    verdict becomes ``needs_human`` instead of ``achieved``."""
    return bool(done_when and _STANDING_DONE_WHEN.search(done_when))


@dataclass(frozen=True)
class InFlight:
    """A reference to an action the engine is currently running for this goal."""

    engine: Engine
    #: the dispatching tool's label — a :data:`GoalTool` (kept ``str``:
    #: re-adoption rebuilds refs from task rows, whose kind is not statically
    #: constrained).
    tool: str
    #: the task_id the engine returned
    id: str
    #: which kind of row to poll. "program" is LEGACY-ONLY: the program/DAG
    #: lane was retired (spec 022 US3) — a persisted pre-022 ref may still
    #: carry it, and polling one now fails loud (the goal blocks with an
    #: actionable reason) rather than crashing the tick.
    ref_kind: Literal["task", "program"]
    goal: str = ""
    #: True when this is the read-only review dispatched by the done-gate (its
    #: terminal result feeds the evaluator, not the next-action planner).
    is_done_check: bool = False


@dataclass(frozen=True)
class GoalStatus:
    """Mutable per-tick state — STATUS.md frontmatter. Overwritten, never appended."""

    phase: Phase = "idle"
    #: the outcome lifecycle stage — canonically a :data:`Lifecycle`
    #: ("executing" is the only live stage since the 008 shrink), but typed
    #: ``str`` because the store round-trips legacy pre-shrink stages verbatim
    #: (fidelity pinned by test_goal_status_migration) and pre-lifecycle rows
    #: load as None (readers treat that as "executing", cf. tick.py)
    lifecycle: Optional[str] = "executing"
    in_flight: Optional[InFlight] = None
    blocked_on: Optional[str] = None
    #: structured classification of the CURRENT block — the machine-readable
    #: sibling of the human-readable ``blocked_on`` prose (a planned auto-heal
    #: pass must never string-match ``blocked_on`` to decide what it may retry).
    #: Taxonomy: ``mechanical:<site>`` (the condition is cheaply re-checkable
    #: without an LLM — ``mechanical:prep`` / ``mechanical:corrupt_doc`` /
    #: ``mechanical:lost_ref`` / ``mechanical:dispatch_cap``); ``needs_answer``
    #: (cognition asked the owner a question); ``bug`` (the force_block
    #: illegal-transition escape hatch). ``""`` = not blocked, or a block that
    #: predates this field / wasn't classified. Only meaningful while
    #: ``phase == "blocked"`` — the store clears it on any write that lands on
    #: a non-blocked phase (see GoalStatusMixin._normalized_blocked_kind).
    blocked_kind: str = ""
    #: auto-heal damping counter (F8): how many times the tick's mechanical
    #: auto-heal has lifted a ``mechanical:*`` block for this goal since a
    #: human last vouched for it. Persisted — a flapping condition
    #: (block → heal → re-block) must not turn the zero-token blocked
    #: steady-state into an LLM call per cycle, so the heal refuses past a
    #: small cap and hands the goal back to the owner (one plain ping,
    #: marked by bumping this one past the cap). Reset to 0 when a HUMAN
    #: lifts a block (steer_goal) and on a productive settle (the same
    #: stability signal that refunds the dispatch cap).
    heal_attempts: int = 0
    #: ISO ts before which the prep-block auto-heal must NOT recheck the
    #: remote — its recheck is a git subprocess (unlike the corrupt-doc
    #: probe, which is free), so it runs on a persisted exponential backoff
    #: (30min · 2^heal_attempts, capped at 6h; see tick_guards._autoheal_prep).
    #: ``None`` = due now. Cleared on every heal and on a human unblock;
    #: between windows a blocked goal stays a zero-subprocess,
    #: zero-cognition tick.
    next_heal_at: Optional[str] = None
    #: consecutive done-gate rounds that did NOT close the goal (the
    #: on_track/off_track resolutions). The done-gate treadmill brake: each
    #: non-closing round increments it; at ``DONEGATE_ROUND_CAP`` the goal
    #: parks for the owner instead of re-advancing forever. Reset on achieved
    #: and when a HUMAN vouches (steer_goal / resume_goal) — never on a
    #: productive settle: every treadmill round settles productively.
    donegate_rounds: int = 0
    #: the most satisfied ``done_when`` clauses any done-gate round of this
    #: goal has reported. A round that beats it is convergence, not churn —
    #: it resets ``donegate_rounds`` to 1 instead of counting toward the cap.
    #: Three refusals at 14/15 with the count still rising is the loop
    #: winning; the brake exists for the treadmill (fresh nits, flat count),
    #: and a pure round counter could not tell them apart (2026-09-02:
    #: devclaw-030 parked at 14/15 and needed a human to say "keep going").
    #: Reset with ``donegate_rounds`` on close and on a human vouch.
    donegate_progress: int = 0
    #: the goal's single OPEN Problem (spec 031), or "" when none. Set in the
    #: same transition that BLOCKs for a human-gated reason; cleared in the
    #: same transition that UNBLOCKs. Read by the tick's blocked branch (the
    #: timebox default — a timestamp compare, zero cognition), by steer_goal
    #: (refused while a Problem is open) and by the read surfaces. The
    #: Problem itself lives in goal_problems; this is the pointer.
    problem_id: str = ""
    #: adapted re-dispatches spent on the sandbox-OOM environment-cap class
    #: (spec 020 FR-002a). The class is deterministic per environment, so the
    #: goal earns exactly ONE re-dispatch whose brief names the cap and
    #: directs bounded tooling; a recurrence past this budget blocks the goal
    #: (``mechanical:env_cap``) instead of grinding. Reset to 0 on a
    #: productive settle (a shipped increment proves the environment now
    #: fits), alongside ``heal_attempts``.
    envcap_redispatches: int = 0
    #: consecutive ticks the dispatch-boundary single-feature-slice guard held
    #: (issue #728). Incremented each tick the scoped check finds offending
    #: feature dirs; reset to 0 when the guard passes or a human steers/resumes.
    #: At ``_SLICE_HOLD_CAP`` consecutive holds the goal transitions to
    #: ``blocked`` with ``blocked_kind="mechanical:slice_hold"`` — loud failure
    #: over silent indefinite sleep.
    slice_hold_count: int = 0
    #: spec 025 merge-on-close: PR URL whose squash-merge is still owed after
    #: an ``achieved`` done-gate verdict. Non-empty ⇒ the advance path retries
    #: the MERGE (zero cognition) instead of planning — the verdict already
    #: stands, so resume must never re-run the done-gate (FR-003). Only legal
    #: outside ``done``; cleared on a successful merge and on cancel.
    pending_merge_pr: str = ""
    #: spec 025 FR-017: the ONE bounded conflict-resolution increment for the
    #: current close attempt has been spent. A second CONFLICT parks the goal
    #: (``mechanical:merge_failed``) instead of dispatching again. Reset on a
    #: successful merge and on steer_goal (a re-direction restarts the budget).
    merge_heal_attempted: bool = False
    #: human note of the intended next step
    next: str = ""
    #: ISO ts of the last time the plan step (LLM) ran
    last_plan_at: Optional[str] = None
    #: ISO ts of the last tick (cheap or not)
    last_tick_at: Optional[str] = None
    #: total engine actions dispatched for this goal — a runaway backstop
    actions_dispatched: int = 0
    #: the last direction-eval verdict + when, surfaced via get_goal (observe surface)
    last_eval_verdict: Optional[EvalVerdict] = None
    last_eval_at: Optional[str] = None
    last_eval_note: str = ""
    #: ISO ts of the last forward progress — a delivery, or (self-initialized by the
    #: watchdog) when the goal first entered executing. The no-progress watchdog
    #: measures wall-clock from here; reset on every delivery. None until executing.
    last_progress_at: Optional[str] = None
    #: True once the no-progress watchdog has pinged the owner for the CURRENT stall;
    #: cleared on the next delivery so a later stall fires again (ping once per stall).
    no_progress_notified: bool = False
    #: Append-only trail of phase transitions — one dict per entry-to-a-new-phase
    #: (``{"phase": str, "at": iso_ts}``). Written by the store on save_status
    #: whenever the phase changes; read by the console for the timeline
    #: timestamps. Deliberately unbounded — the log is human-scale (dozens of
    #: entries per goal at most).
    phase_history: tuple[dict, ...] = ()
    #: the stored State value (see devclaw.goal.transitions) — None on a
    #: a status object never round-tripped through the store.
    #: compare=False: two GoalStatus objects with identical business fields
    #: still compare equal regardless of this projection (existing tests
    #: build expected GoalStatus(...) objects without ever setting it — see
    #: tests/test_goal_status_migration.py's `migrated == GoalStatus()`).
    state: Optional[str] = field(default=None, compare=False)
    #: optimistic-concurrency counter GoalStore.transition() CAS's against —
    #: bumped by exactly 1 on every store write (save_status / transition /
    #: update_status_fields). compare=False for the same reason as `state`.
    version: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Action:
    """One engine call the tick dispatches."""

    engine: Engine
    tool: GoalTool
    goal: str
    verify_cmd: Optional[str] = None
    open_pr: bool = True
    #: True when the action is generated scaffolding — threads onto the task
    #: row so the queue skips the adversarial review gate for it. SAFETY:
    #: skips review ONLY — verify + test-integrity still run.
    scaffold: bool = False


@dataclass(frozen=True)
class ClauseVerdict:
    """One atomic ``done_when`` clause + the evaluator's per-clause finding.

    At the done-gate the evaluator decomposes ``done_when`` into independent
    requirements joined by AND and grades each one against the repo-review
    evidence. The aggregate verdict on the parent :class:`EvalResult` is then
    derived from these: ``achieved`` requires every clause to be satisfied with
    non-empty evidence; any unsatisfied clause forces ``off_track`` with that
    clause cited in the corrections (closes the 2026-06-25 "stub everything"
    failure mode)."""

    clause: str
    satisfied: bool
    #: file path(s) + symbol/test name(s) confirming satisfaction, OR an explicit
    #: "missing — should live in <where>" note when unsatisfied. Vague prose is
    #: rejected by the evaluator prompt; a non-empty string here is the evidence
    #: contract.
    evidence: str = ""
    #: spec 031: the ``goal_decisions.id`` that settled this clause, when the
    #: done-gate graded it ``resolved_by_decision`` (an owner's recorded
    #: ruling, cited as the evidence, never re-litigated). "" otherwise.
    resolved_by: str = ""


@dataclass(frozen=True)
class ProblemOption:
    """One bounded resolution a Problem offers (spec 031)."""

    key: str
    label: str
    consequence: str = ""
    #: true only for the option that would close the goal — drives the
    #: strictness-dial rule for a DEFAULTED decision (Q2 → C)
    closes_goal: bool = False


@dataclass(frozen=True)
class Problem:
    """What a human-gated block says, typed (spec 031 US1).

    Raised only by ``goal.problems.raise_problem`` inside the caller's BLOCK
    transaction; exactly one is ``open`` per goal (``GoalStatus.problem_id``).
    """

    id: str
    goal_id: str
    #: the human-gated blocked_kind it accompanies
    kind: str
    #: done_gate | churn_park | worker_block | dispatch_park | admission_lint
    raised_by: str
    what: str
    #: the done_when clause concerned; "" = contract-level
    clause: str
    why: str
    options: tuple[ProblemOption, ...]
    default_key: str
    timebox_at: int
    #: open | resolved | defaulted | superseded
    status: str = "open"
    raised_at: int = 0
    closed_at: Optional[int] = None
    closed_by_decision: str = ""

    @property
    def default(self) -> ProblemOption:
        for o in self.options:
            if o.key == self.default_key:
                return o
        return self.options[0]


@dataclass(frozen=True)
class Decision:
    """The recorded answer to a Problem — devclaw-controlled, fed forward,
    supersedable (spec 031 US2/US4)."""

    id: str
    goal_id: str
    problem_id: str
    clause: str
    #: correct_implementation | decide
    verb: str
    option_key: str = ""
    text: str = ""
    #: owner | defaulted | admission
    provenance: str = "owner"
    made_by: str = ""
    made_at: int = 0
    superseded_by: str = ""


@dataclass(frozen=True)
class EvalResult:
    """The direction evaluator's verdict — grounded in delivered artifacts, not
    in backlog-counts. ``verdict`` drives the loop: ``achieved`` closes the goal;
    ``off_track`` writes ``corrections`` into the inbox as steering; ``stalled``
    and ``needs_human`` block; ``on_track`` just records and continues."""

    verdict: EvalVerdict
    rationale: str = ""
    #: concrete corrections / new direction the evaluator wants pursued — written
    #: to inbox.md as steering so the next-action planner honors them.
    corrections: list[str] = field(default_factory=list)
    #: present when verdict == "needs_human"
    question: str = ""
    #: per-clause findings — populated at the done-gate. Empty pre-done-gate.
    clauses: list[ClauseVerdict] = field(default_factory=list)
    #: axis-B verdict at the done-gate: ``clean`` | ``concerns`` | ``poor``.
    #: Empty pre-done-gate. When ``poor`` (or ``concerns`` with substantive named
    #: items), ``validate()`` mechanically downgrades ``achieved`` → ``off_track``
    #: — the second half of the closeloop-D1/D2/D6 safety net (the model has an
    #: incentive to declare done; mechanism has none).
    structural_health: str = ""
    #: itemized structural concerns backing ``structural_health``. Empty when
    #: ``clean``. When ``poor`` or a substantive ``concerns``, each entry becomes
    #: a correction on the downgrade path.
    structural_concerns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PollResult:
    """Outcome of polling an in-flight engine ref."""

    terminal: bool
    #: pending | running | done | failed | cancelled | planning | ...
    status: str
    #: the engine's full result/error detail, surfaced to cognition on terminal
    detail: str = ""
    #: delivery evidence — the PR url the engine opened (None if not delivered)
    pr_url: Optional[str] = None
    #: verify-gate verdict (None if no gate ran)
    gate_passed: Optional[bool] = None
    #: gate-time diff stats the queue captured (files/insertions/deletions),
    #: None when absent — a stats hiccup never blocks a settle
    diff_stats: Optional[dict] = None
    #: the worker's REPO NOTES hand-back — durable repo-level facts for future
    #: tasks on the same repo (MC borrow item 3). None when the worker
    #: reported none.
    repo_notes: Optional[str] = None
    #: the code-writing task finished successfully having changed NOTHING
    #: (spec 013 FR-014). A settle carrying this is not a delivered increment:
    #: it publishes nothing and must read upstream as no progress, so a run that
    #: accomplished nothing cannot masquerade as work. False for read-only kinds
    #: (a review legitimately changes nothing) and for engines that predate it.
    no_change: bool = False
    #: the worker blocked on the context tripwire having LANDED a coherent
    #: partial increment — a determinable, non-empty span committed onto the
    #: goal branch, which is the branch the next action is placed on. Distinct
    #: from a bare block (nothing landed) and from ``done`` (nothing shipped,
    #: no gate passed): the settle stays failed-closed, but upstream reads it
    #: as forward progress worth continuing from rather than a wasted dispatch.
    #: False for engines that predate the flag.
    landed_partial: bool = False

    @property
    def running(self) -> bool:
        return not self.terminal
