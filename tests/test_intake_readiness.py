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

import pytest

from devclaw import intake, intake_readiness
from devclaw.project_registry import ProjectRegistry


# ---- fakes ------------------------------------------------------------------

class FakeGh:
    """Records label/comment writes and serves a canned issue body for reads."""

    def __init__(self, body: str | None = None):
        self.body = body
        self.labels_ensured: list[tuple[str, str]] = []
        self.added: list[tuple[str, str, tuple[str, ...]]] = []
        self.removed: list[tuple[str, str, tuple[str, ...]]] = []
        self.comments: list[tuple[str, str, str]] = []

    async def ensure_label(self, repo, name):
        self.labels_ensured.append((repo, name))

    async def add_labels(self, repo, issue, labels):
        self.added.append((repo, issue, tuple(labels)))

    async def remove_labels(self, repo, issue, labels):
        self.removed.append((repo, issue, tuple(labels)))

    async def comment(self, repo, issue, body):
        self.comments.append((repo, issue, body))

    async def read_issue(self, repo, issue):
        return self.body

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
