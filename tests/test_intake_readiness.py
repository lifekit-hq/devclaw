"""Intake readiness gate — spec 006 (P1 of the autonomous issue-driven pipeline).

Named regression tests for the behaviors the spec locks:
- a groundable ask grades ``devclaw-ready``; a well-formed-but-ungroundable ask
  grades ``needs-refinement`` with an actionable reason (US1 / FR-002..004);
- every failure path fails CLOSED to ``needs-refinement``, never ``devclaw-ready``
  — evaluator crash, malformed output, missing repo context, paused cognition
  (US2 / FR-005 / FR-008 / FR-011);
- the manual re-trigger re-reads the amended issue and swaps the label
  (US3 / FR-010);
- the grade is wired on the intake path only — the zero-token idle guard is
  untouched (FR-009).

Zero network — the gh adapter is a fake and the claude caller is injected, per
the ``goal_fakes`` / ``self_issue`` house pattern.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from devclaw import intake, intake_readiness
from devclaw.project_registry import ProjectRegistry


# ---- fakes ------------------------------------------------------------------

class FakeGh:
    """Records label/comment writes and serves a canned issue body for reads.
    ``pending`` is the ungraded-issue set the recovery sweep discovers;
    ``unreadable`` issues return no body (models a gh read hiccup)."""

    def __init__(
        self,
        body: str | None = None,
        pending=None,
        unreadable=(),
        title: str = "",
        state: str = "OPEN",
    ):
        self.body = body
        self.title = title
        self.state = state
        self.pending = list(pending or [])
        self.unreadable = set(unreadable)
        self.list_calls: list[str] = []
        self.labels_ensured: list[tuple[str, str]] = []
        self.added: list[tuple[str, str, tuple[str, ...]]] = []
        self.removed: list[tuple[str, str, tuple[str, ...]]] = []
        self.comments: list[tuple[str, str, str]] = []

    async def list_intake_awaiting_grade(self, repo):
        self.list_calls.append(repo)
        return list(self.pending)

    async def ensure_label(self, repo, name):
        self.labels_ensured.append((repo, name))

    async def add_labels(self, repo, issue, labels):
        self.added.append((repo, issue, tuple(labels)))

    async def remove_labels(self, repo, issue, labels):
        self.removed.append((repo, issue, tuple(labels)))

    async def comment(self, repo, issue, body):
        self.comments.append((repo, issue, body))

    async def view_issue(self, repo, issue):
        if issue in self.unreadable or self.body is None:
            return None
        return {"title": self.title, "body": self.body, "state": self.state}

    def added_labels(self) -> list[str]:
        return [l for _, _, labels in self.added for l in labels]


class RaisingClaude:
    """A caller that raises — models an evaluator crash / a usage-limit pause
    (both surface as an exception out of the cognition seam)."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError("boom")
        self.calls = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        raise self.exc


class CannedClaude:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0
        self.last_prompt = ""

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        return self.response


READY_JSON = '{"ready": true, "missing": [], "rationale": "names Foo.cs and the change"}'
NOT_READY_JSON = (
    '{"ready": false, "missing": ["no locatable surface named", '
    '"no concrete change described"], "rationale": "too vague to firm"}'
)

REPO_CTX = "workspace_dir: /ws\ngit_branch: main\ntracked_top_level: src, tests"


def _grade(**overrides):
    kwargs = dict(
        repo="lifekit-hq/finance-sentry",
        issue="https://github.com/lifekit-hq/finance-sentry/issues/7",
        what="Fix stale balances returned by the refresh endpoint.",
        done_when="A regression test proves refreshed balances are served.",
        context=None,
        workspace_dir="/ws",
        repo_context=REPO_CTX,
    )
    kwargs.update(overrides)
    gh = kwargs.pop("gh")
    return asyncio.run(intake.grade_and_label(gh=gh, **kwargs))


# ---- US1: the core grade ----------------------------------------------------

def test_groundable_ask_is_graded_devclaw_ready():
    gh = FakeGh()
    label = _grade(gh=gh, claude_caller=CannedClaude(READY_JSON))
    assert label == intake.READY_LABEL
    assert intake.READY_LABEL in gh.added_labels()
    # the opposite readiness label is swapped out so a re-grade flips cleanly
    assert (
        "lifekit-hq/finance-sentry",
        "https://github.com/lifekit-hq/finance-sentry/issues/7",
        (intake.NEEDS_REFINEMENT_LABEL,),
    ) in gh.removed


def test_ungroundable_ask_is_graded_needs_refinement():
    gh = FakeGh()
    label = _grade(gh=gh, claude_caller=CannedClaude(NOT_READY_JSON))
    assert label == intake.NEEDS_REFINEMENT_LABEL
    assert intake.NEEDS_REFINEMENT_LABEL in gh.added_labels()
    assert intake.READY_LABEL not in gh.added_labels()


def test_needs_refinement_carries_actionable_missing_element():
    gh = FakeGh()
    _grade(gh=gh, claude_caller=CannedClaude(NOT_READY_JSON))
    (_, _, comment) = gh.comments[-1]
    assert "needs-refinement" in comment
    # at least one concrete, asker-fixable missing element is surfaced (FR-004)
    assert "no locatable surface named" in comment


# ---- US2: fail closed -------------------------------------------------------

def test_readiness_evaluator_crash_fails_closed_to_needs_refinement():
    gh = FakeGh()
    caller = RaisingClaude()
    label = _grade(gh=gh, claude_caller=caller)
    assert caller.calls == 1  # it really tried
    assert label == intake.NEEDS_REFINEMENT_LABEL
    assert intake.READY_LABEL not in gh.added_labels()


def test_malformed_evaluator_output_fails_closed_to_needs_refinement():
    gh = FakeGh()
    label = _grade(gh=gh, claude_caller=CannedClaude("not json, no verdict here"))
    assert label == intake.NEEDS_REFINEMENT_LABEL
    assert intake.READY_LABEL not in gh.added_labels()


def test_ready_true_but_no_json_object_never_lands_ready():
    # an evaluator that emits an affirmative sentence but no JSON must NOT ship
    # devclaw-ready — non-JSON is unusable output → fail closed.
    gh = FakeGh()
    label = _grade(gh=gh, claude_caller=CannedClaude("Yes, this is devclaw-ready!"))
    assert label == intake.NEEDS_REFINEMENT_LABEL


def test_missing_repo_context_fails_closed_with_distinct_reason():
    gh = FakeGh()
    caller = CannedClaude(READY_JSON)  # would say ready — but must never be asked
    label = _grade(gh=gh, claude_caller=caller, repo_context="")
    assert label == intake.NEEDS_REFINEMENT_LABEL
    assert caller.calls == 0  # short-circuit: no repo facts ⇒ ungroundable, no token
    (_, _, comment) = gh.comments[-1]
    # the reason distinguishes "couldn't read the repo" from "ask is vague"
    assert "could not read the target repository" in comment


def test_paused_cognition_defers_grade_to_needs_refinement_not_ready():
    # a usage-limit pause surfaces as an exception from the caller → fail closed.
    gh = FakeGh()
    from devclaw.planner import PlannerError

    caller = RaisingClaude(PlannerError("Claude usage limit reached; quota resets"))
    label = _grade(gh=gh, claude_caller=caller)
    assert label == intake.NEEDS_REFINEMENT_LABEL
    assert intake.READY_LABEL not in gh.added_labels()


def test_grade_and_label_never_raises_even_when_gh_label_add_fails():
    class BrokenGh(FakeGh):
        async def add_labels(self, repo, issue, labels):
            raise RuntimeError("network down")

    gh = BrokenGh()
    # must return a label (needs-refinement) rather than propagate the gh error
    label = _grade(gh=gh, claude_caller=CannedClaude(NOT_READY_JSON))
    assert label == intake.NEEDS_REFINEMENT_LABEL


# ---- FR-011: filing returns the receipt even when cognition is unavailable --

def test_intake_receipt_returned_even_when_cognition_paused(tmp_path):
    """file_intake itself makes ZERO cognition calls — the grade is a separate,
    async step. So a paused cognition backend cannot block or fail the receipt."""
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )

    class FileGh:
        async def ensure_label(self, repo, name):
            return None

        async def create_issue(self, repo, *, title, body, labels):
            return "https://github.com/lifekit-hq/finance-sentry/issues/9"

    result = asyncio.run(
        intake.file_intake(
            reg,
            gh=FileGh(),
            project_id="finance-sentry",
            what="Add a health endpoint to the API.",
            done_when="A test asserts GET /health returns 200.",
            asker="denys",
            channel="chat",
            now_ms=1_755_000_000_000,
        )
    )
    assert result["issue_url"].endswith("/issues/9")


# ---- US3 / FR-010: manual re-trigger swaps the label ------------------------

def _intake_body(what: str, done_when: str) -> str:
    return intake.issue_body(
        what=what,
        done_when=done_when,
        context=None,
        asker="denys",
        channel="chat",
        project_id="finance-sentry",
        slug="lifekit-hq/finance-sentry",
        filed_ms=1_755_000_000_000,
    )


def test_regrade_swaps_label_when_amended_ask_becomes_groundable(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    amended = _intake_body(
        "Rename Balance.cs to AccountBalance.cs and update its callers.",
        "The type is renamed and the build passes.",
    )
    gh = FakeGh(body=amended)
    result = asyncio.run(
        intake.regrade(
            reg,
            project_id="finance-sentry",
            issue="https://github.com/lifekit-hq/finance-sentry/issues/7",
            claude_caller=CannedClaude(READY_JSON),
            gh=gh,
        )
    )
    assert result["readiness"] == intake.READY_LABEL
    assert intake.READY_LABEL in gh.added_labels()
    # the stale needs-refinement label is removed on the swap
    assert (intake.NEEDS_REFINEMENT_LABEL,) in [labels for _, _, labels in gh.removed]


def test_regrade_reads_the_amended_issue_body_on_demand(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    body = _intake_body("Do the thing to Foo.cs.", "It works and a test proves it.")
    gh = FakeGh(body=body)
    caller = CannedClaude(READY_JSON)
    asyncio.run(
        intake.regrade(
            reg,
            project_id="finance-sentry",
            issue="https://github.com/lifekit-hq/finance-sentry/issues/7",
            claude_caller=caller,
            gh=gh,
        )
    )
    # the current issue text (not a re-supplied ask) was what got graded
    assert "Do the thing to Foo.cs." in caller.last_prompt


def test_regrade_fails_loud_when_issue_cannot_be_read(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    gh = FakeGh(body=None)  # gh could not read the issue
    with pytest.raises(intake.IntakeError, match="could not read issue"):
        asyncio.run(
            intake.regrade(
                reg,
                project_id="finance-sentry",
                issue="https://github.com/lifekit-hq/finance-sentry/issues/7",
                claude_caller=CannedClaude(READY_JSON),
                gh=gh,
            )
        )


def test_parse_issue_fields_recovers_what_done_when_context():
    body = intake.issue_body(
        what="Line one of the ask.\nmore detail",
        done_when="A test proves it works and is merged.",
        context="Seen in prod on 2026-08-15.",
        asker="denys",
        channel="chat",
        project_id="p",
        slug="o/n",
        filed_ms=1_755_000_000_000,
    )
    what, done_when, context = intake.parse_issue_fields(body)
    assert what.startswith("Line one of the ask.")
    assert "A test proves it works" in done_when
    assert context == "Seen in prod on 2026-08-15."


# ---- FR-009: the grade is on the intake path ONLY (zero-token idle guard) ---

def test_readiness_grade_adds_no_idle_tick_cognition():
    """The readiness grade must never be wired into the heartbeat/tick path —
    that is what keeps an idle goal at zero cognition. Guard structurally: no
    tick-path module references the readiness gate."""
    from devclaw.goal import tick, tick_guards, tick_dispatch, service

    for mod in (tick, tick_guards, tick_dispatch, service):
        src = inspect.getsource(mod)
        assert "intake_readiness" not in src, f"{mod.__name__} reaches the readiness gate"
        assert "grade_and_label" not in src, f"{mod.__name__} reaches the readiness gate"


# ---- prompt content: grounding present, no leaked header, no done_when grade -

def test_readiness_prompt_carries_grounding_clause_and_repo_facts():
    prompt = intake_readiness.build_prompt(
        what="Fix Foo.cs", done_when="a test passes", repo_context=REPO_CTX
    )
    # the #227 grounding clause is present
    assert "any repository you have seen before" in prompt
    assert "absent ⇒" in prompt
    # repo facts are grounded in the injected context block
    assert "tracked_top_level: src, tests" in prompt
    # non-overlap: readiness explicitly does NOT derive done_when / a checklist
    assert "firming phase owns that" in prompt


def test_readiness_prompt_omits_repo_facts_when_absent():
    # prove the marker is absent from the raw template first (non-vacuous test)
    from devclaw.prompts import _read

    assert "tracked_top_level" not in _read("intake-readiness")
    prompt = intake_readiness.build_prompt(
        what="Fix Foo.cs", done_when="a test passes", repo_context=""
    )
    assert "tracked_top_level" not in prompt
    assert "repo facts are unknown" in prompt


# ---- P2 hardening: durable recovery of ungraded intake issues (SC-001) ------
# The P1 grade is an in-process async task; a restart between the receipt and the
# grade landing would leave the issue with the intake label but no readiness
# label. recover_pending_grades reconciles that set (derived from GitHub itself)
# at serve-start, so no ask is left in permanent unlabeled limbo.

def _reg_with_project(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    return reg


def test_list_intake_awaiting_grade_drops_already_graded(monkeypatch):
    """The pending set is derived from the labels: an intake issue is awaiting a
    grade iff it carries NEITHER readiness label. Graded issues are excluded."""
    payload = json.dumps(
        [
            {"url": "https://x/issues/1", "labels": [{"name": "devclaw-intake"}]},
            {"url": "https://x/issues/2", "labels": [{"name": "devclaw-intake"}, {"name": "devclaw-ready"}]},
            {"url": "https://x/issues/3", "labels": [{"name": "devclaw-intake"}, {"name": "needs-refinement"}]},
        ]
    )

    async def fake_run(*args):
        return 0, payload

    monkeypatch.setattr(intake, "_run", fake_run)
    pending = asyncio.run(intake.GhCli().list_intake_awaiting_grade("o/n"))
    assert pending == ["https://x/issues/1"]


def test_list_intake_awaiting_grade_degrades_to_empty_on_gh_error(monkeypatch):
    async def fake_run(*args):
        return 1, "gh: not authenticated"

    monkeypatch.setattr(intake, "_run", fake_run)
    assert asyncio.run(intake.GhCli().list_intake_awaiting_grade("o/n")) == []


def test_recover_pending_grades_regrades_every_ungraded_intake_issue(tmp_path):
    reg = _reg_with_project(tmp_path)
    body = _intake_body("Rename Balance.cs to AccountBalance.cs.", "Build passes and a test proves it.")
    gh = FakeGh(
        body=body,
        pending=[
            "https://github.com/lifekit-hq/finance-sentry/issues/7",
            "https://github.com/lifekit-hq/finance-sentry/issues/8",
        ],
    )
    caller = CannedClaude(READY_JSON)
    n = asyncio.run(intake.recover_pending_grades(reg, gh=gh, claude_caller=caller))
    assert n == 2
    assert caller.calls == 2
    assert gh.added_labels().count(intake.READY_LABEL) == 2


def test_recover_pending_grades_spends_zero_cognition_when_nothing_pending(tmp_path):
    reg = _reg_with_project(tmp_path)
    gh = FakeGh(body=_intake_body("x", "y"), pending=[])
    caller = CannedClaude(READY_JSON)
    n = asyncio.run(intake.recover_pending_grades(reg, gh=gh, claude_caller=caller))
    assert n == 0
    assert caller.calls == 0  # no ungraded issue ⇒ no claude call at all


def test_recover_pending_grades_is_best_effort_per_issue(tmp_path):
    reg = _reg_with_project(tmp_path)
    body = _intake_body("Do the thing to Foo.cs.", "It works and a test proves it.")
    good = "https://github.com/lifekit-hq/finance-sentry/issues/7"
    bad = "https://github.com/lifekit-hq/finance-sentry/issues/9"  # unreadable
    gh = FakeGh(body=body, pending=[bad, good], unreadable={bad})
    n = asyncio.run(
        intake.recover_pending_grades(reg, gh=gh, claude_caller=CannedClaude(READY_JSON))
    )
    assert n == 1  # the unreadable issue is skipped; the good one is graded
    assert intake.READY_LABEL in gh.added_labels()


def test_recover_pending_grades_skips_repo_on_list_error_and_never_raises(tmp_path):
    reg = _reg_with_project(tmp_path)

    class ListBrokenGh(FakeGh):
        async def list_intake_awaiting_grade(self, repo):
            raise RuntimeError("gh list exploded")

    gh = ListBrokenGh(body=_intake_body("x", "y"))
    n = asyncio.run(
        intake.recover_pending_grades(reg, gh=gh, claude_caller=CannedClaude(READY_JSON))
    )
    assert n == 0  # never raises; the bad repo is simply skipped


def test_readiness_recovery_is_not_wired_into_the_idle_tick():
    """Recovery runs at serve-start only — never on the heartbeat tick, so the
    zero-token idle guard stays intact."""
    from devclaw.goal import tick, tick_guards, tick_dispatch, service

    for mod in (tick, tick_guards, tick_dispatch, service):
        src = inspect.getsource(mod)
        assert "recover_pending_grades" not in src, f"{mod.__name__} reaches recovery"


def test_serve_start_kicks_the_readiness_recovery_sweep(monkeypatch):
    """The serve path schedules the recovery sweep as a background task under the
    running loop, calling recover_pending_grades exactly once."""
    from devclaw.server import lifecycle

    called = {"n": 0}

    async def fake_recover(registry, **kw):
        called["n"] += 1
        return 0

    monkeypatch.setattr(lifecycle.intake_mod, "recover_pending_grades", fake_recover)

    async def drive():
        lifecycle._kick_readiness_recovery()
        await asyncio.sleep(0)
        for t in list(lifecycle._RECOVERY_TASKS):
            await t

    asyncio.run(drive())
    assert called["n"] == 1


# ---- spec 009: universal issue adoption (format-tolerant regrade) -----------

def _registered(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    return reg


ISSUE_7 = "https://github.com/lifekit-hq/finance-sentry/issues/7"


def test_regrade_adopts_plain_issue_without_what_section(tmp_path):
    """A hand-written issue (no intake sections) grades via the title+body
    fallback: the ask is the issue as it stands, done_when renders as absent,
    and the readiness label + mirror comment land exactly as for intake-filed
    issues (spec 009 FR-001/FR-002)."""
    reg = _registered(tmp_path)
    gh = FakeGh(
        title="Refresh endpoint returns stale balances",
        body="After POST /refresh, GET /balances still serves the old numbers.",
    )
    caller = CannedClaude(READY_JSON)
    result = asyncio.run(
        intake.regrade(
            reg, project_id="finance-sentry", issue=ISSUE_7,
            claude_caller=caller, gh=gh,
        )
    )
    assert result["readiness"] == intake.READY_LABEL
    assert intake.READY_LABEL in gh.added_labels()
    assert gh.comments  # the verdict mirror comment lands on adopted issues too
    # title AND body both became the ask
    assert "Refresh endpoint returns stale balances" in caller.last_prompt
    assert "still serves the old numbers" in caller.last_prompt
    # no done_when was fabricated — the prompt shows it as absent
    assert "(none provided)" in caller.last_prompt


def test_regrade_intake_format_issue_behavior_unchanged(tmp_path):
    """SC-003: an issue WITH intake sections never touches the fallback — the
    structured what/done_when are honored exactly as before the adoption
    change (the issue title is NOT folded into the ask)."""
    reg = _registered(tmp_path)
    body = _intake_body(
        "Rename Balance.cs to AccountBalance.cs and update its callers.",
        "The type is renamed and the build passes.",
    )
    gh = FakeGh(body=body, title="[intake] rename balance type")
    caller = CannedClaude(READY_JSON)
    result = asyncio.run(
        intake.regrade(
            reg, project_id="finance-sentry", issue=ISSUE_7,
            claude_caller=caller, gh=gh,
        )
    )
    assert result["readiness"] == intake.READY_LABEL
    assert "Rename Balance.cs to AccountBalance.cs" in caller.last_prompt
    assert "The type is renamed and the build passes." in caller.last_prompt
    # structured done_when means the absent-marker must NOT appear
    assert "(none provided)" not in caller.last_prompt.split("context:")[0]
    # the GitHub title is not folded into a structured ask
    assert "[intake] rename balance type" not in caller.last_prompt


def test_regrade_rejects_closed_issue_loudly(tmp_path):
    """Adoption targets open work: a non-OPEN issue is a loud IntakeError
    BEFORE any cognition is spent (spec 009 edge case)."""
    reg = _registered(tmp_path)
    gh = FakeGh(title="Old bug", body="Long fixed.", state="CLOSED")
    caller = CannedClaude(READY_JSON)
    with pytest.raises(intake.IntakeError, match="not open"):
        asyncio.run(
            intake.regrade(
                reg, project_id="finance-sentry", issue=ISSUE_7,
                claude_caller=caller, gh=gh,
            )
        )
    assert caller.calls == 0
    assert gh.added == []


# ---- spec 009 US2: bulk backlog onboarding (grade_backlog) -------------------

class BacklogGh(FakeGh):
    """Serves a whole backlog: ``issues`` maps url -> {title, body, state,
    labels, createdAt, unreadable}. ``list_open_issues`` renders the gh listing
    shape; ``view_issue`` serves per-issue."""

    def __init__(self, issues: dict, list_fails: bool = False):
        super().__init__()
        self.issues = issues
        self.list_fails = list_fails

    async def list_open_issues(self, repo):
        if self.list_fails:
            return None
        return [
            {
                "url": url,
                "labels": [{"name": n} for n in meta.get("labels", ())],
                "createdAt": meta.get("createdAt", ""),
            }
            for url, meta in self.issues.items()
        ]

    async def view_issue(self, repo, issue):
        meta = self.issues.get(issue)
        if meta is None or meta.get("unreadable"):
            return None
        return {
            "title": meta.get("title", "t"),
            "body": meta.get("body", "b"),
            "state": meta.get("state", "OPEN"),
        }


def _u(n: int) -> str:
    return f"https://github.com/lifekit-hq/finance-sentry/issues/{n}"


def test_grade_backlog_caps_batch_and_reports_remainder_without_continuing(
    tmp_path, monkeypatch
):
    """One invocation grades at most the cap, priority-band-first then oldest;
    the remainder is named in the report and NOT graded — continuation is only
    ever a fresh explicit invocation (spec 009 FR-007a)."""
    monkeypatch.setattr(intake, "BULK_GRADE_CAP", 2)
    reg = _registered(tmp_path)
    gh = BacklogGh({
        _u(1): {"labels": ("P1",), "createdAt": "2026-01-02T00:00:00Z"},
        _u(2): {"labels": ("P0",), "createdAt": "2026-01-03T00:00:00Z"},
        _u(3): {"labels": (), "createdAt": "2026-01-01T00:00:00Z"},
        _u(4): {"labels": ("P1",), "createdAt": "2026-01-01T00:00:00Z"},
        _u(5): {"labels": (intake.READY_LABEL,), "createdAt": "2026-01-01T00:00:00Z"},
    })
    caller = CannedClaude(READY_JSON)
    report = asyncio.run(
        intake.grade_backlog(
            reg, project_id="finance-sentry", gh=gh, claude_caller=caller
        )
    )
    # P0 first, then the older P1; unlabeled sorts last
    assert report["graded_ready"] == [_u(2), _u(4)]
    assert report["not_yet_graded"] == [_u(1), _u(3)]
    assert report["skipped_already_graded"] == [_u(5)]
    assert report["cap"] == 2
    assert caller.calls == 2  # exactly the batch — no automatic continuation


def test_grade_backlog_skips_graded_and_spends_zero_cognition_when_none_pending(
    tmp_path,
):
    reg = _registered(tmp_path)
    gh = BacklogGh({
        _u(1): {"labels": (intake.READY_LABEL,)},
        _u(2): {"labels": (intake.NEEDS_REFINEMENT_LABEL, "P0")},
    })
    caller = CannedClaude(READY_JSON)
    report = asyncio.run(
        intake.grade_backlog(
            reg, project_id="finance-sentry", gh=gh, claude_caller=caller
        )
    )
    assert caller.calls == 0
    assert report["graded_ready"] == [] and report["graded_needs_refinement"] == []
    assert sorted(report["skipped_already_graded"]) == [_u(1), _u(2)]
    assert report["not_yet_graded"] == []


def test_grade_backlog_resumes_by_rederiving_pending_from_labels(
    tmp_path, monkeypatch
):
    """No progress store: the second invocation re-derives the pending set from
    the labels GitHub now carries and grades exactly the remainder."""
    monkeypatch.setattr(intake, "BULK_GRADE_CAP", 2)
    reg = _registered(tmp_path)
    issues = {
        _u(1): {"labels": ("P0",), "createdAt": "2026-01-01T00:00:00Z"},
        _u(2): {"labels": ("P0",), "createdAt": "2026-01-02T00:00:00Z"},
        _u(3): {"labels": ("P1",), "createdAt": "2026-01-01T00:00:00Z"},
    }
    gh = BacklogGh(issues)
    caller = CannedClaude(READY_JSON)
    first = asyncio.run(
        intake.grade_backlog(
            reg, project_id="finance-sentry", gh=gh, claude_caller=caller
        )
    )
    assert first["graded_ready"] == [_u(1), _u(2)]
    assert first["not_yet_graded"] == [_u(3)]
    # the labels landed on GitHub (simulate what the first batch wrote)
    for url in first["graded_ready"]:
        issues[url]["labels"] = issues[url]["labels"] + (intake.READY_LABEL,)
    second = asyncio.run(
        intake.grade_backlog(
            reg, project_id="finance-sentry", gh=gh, claude_caller=caller
        )
    )
    assert second["graded_ready"] == [_u(3)]  # exactly the remainder
    assert sorted(second["skipped_already_graded"]) == [_u(1), _u(2)]
    assert caller.calls == 3  # 2 + 1 — nothing re-graded


def test_grade_backlog_one_issue_failure_never_stops_the_batch(tmp_path):
    """A mid-batch unreadable issue lands in failed[] with a reason; the rest
    of the batch still grades (recovery-sweep convention)."""
    reg = _registered(tmp_path)
    gh = BacklogGh({
        _u(1): {"labels": ("P0",), "createdAt": "2026-01-01T00:00:00Z"},
        _u(2): {"labels": ("P0",), "createdAt": "2026-01-02T00:00:00Z", "unreadable": True},
        _u(3): {"labels": ("P1",), "createdAt": "2026-01-01T00:00:00Z"},
    })
    caller = CannedClaude(READY_JSON)
    report = asyncio.run(
        intake.grade_backlog(
            reg, project_id="finance-sentry", gh=gh, claude_caller=caller
        )
    )
    assert report["graded_ready"] == [_u(1), _u(3)]
    assert [f["url"] for f in report["failed"]] == [_u(2)]
    assert "could not read" in report["failed"][0]["reason"]


def test_grade_backlog_rejects_loudly_when_listing_fails(tmp_path):
    """An explicit operator action never silently degrades to an empty sweep."""
    reg = _registered(tmp_path)
    gh = BacklogGh({}, list_fails=True)
    with pytest.raises(intake.IntakeError, match="could not list open issues"):
        asyncio.run(
            intake.grade_backlog(
                reg, project_id="finance-sentry", gh=gh,
                claude_caller=CannedClaude(READY_JSON),
            )
        )
