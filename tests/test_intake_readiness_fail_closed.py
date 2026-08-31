"""The intake readiness grade fails CLOSED — a tripwire, not a coverage test.

The invariant: an ask that would waste a sandbox session must not earn
``devclaw-ready``. Grading is the last cheap checkpoint before devclaw spends a
real worker run, so every axis that can refuse must refuse HERE rather than
letting the work reach the done-gate.

Spec 028 US2 adds the staleness axis: an ask the repository already satisfies
is not ready, however well it grounds (FR-007).
Issue #769 adds the contract-section axis: a body the done-gate's
``extract_acceptance`` cannot parse cannot grade ready — one definition of "has
a contract", pre-cognition. Extend THIS test's cases when a new refusal axis
lands — do not mint a sibling file.
"""

import asyncio

import pytest

from devclaw import intake, intake_readiness
from devclaw.intake import NEEDS_REFINEMENT_LABEL, NO_CONTRACT_REASON
from devclaw.intake_readiness import STALE_REASON, validate


def _output(**over):
    """A well-formed, otherwise-READY grader response — so every case below
    isolates exactly one refusal axis."""
    base = {
        "ready": True,
        "missing": [],
        "rationale": "grounded against the repo",
        "stale": False,
        "increments": {"assessed": 1, "agrees": True, "basis": "one change"},
    }
    base.update(over)
    return base


def test_stale_issue_graded_not_ready_when_condition_already_resolved():
    """FR-007: staleness overrides an otherwise-ready verdict, and the refusal
    carries the concrete, asker-fixable reason."""
    verdict = validate(_output(stale=True))

    assert verdict.ready is False, "a stale ask must never grade ready"
    assert verdict.stale is True
    assert STALE_REASON in verdict.missing
    # The objection leads, so the asker sees it before any grounding nitpick.
    assert verdict.missing[0] == STALE_REASON


def test_stale_refusal_survives_a_grounding_complaint_without_duplicating():
    """A stale ask that ALSO fails grounding keeps both objections, once each."""
    verdict = validate(
        _output(ready=False, missing=["no locatable surface named"], stale=True)
    )

    assert verdict.ready is False
    assert verdict.missing.count(STALE_REASON) == 1
    assert "no locatable surface named" in verdict.missing


def test_sizing_disagreement_never_makes_an_ask_stale():
    """FR-009: the axes are independent — a disputed count is not staleness."""
    verdict = validate(
        _output(increments={"assessed": 4, "agrees": False, "basis": "four PRs"})
    )

    assert verdict.stale is False
    assert verdict.ready is True, "a sizing dispute never moves the verdict"
    assert verdict.sizing.agrees is False


@pytest.mark.parametrize(
    "raw",
    [None, "", "false", "no", 0, 1, [], {}, "maybe"],
    ids=["absent", "empty", "false-str", "no", "zero", "one", "list", "dict", "hedge"],
)
def test_absent_or_garbled_staleness_signal_does_not_block_a_ready_ask(raw):
    """FR-010: staleness needs an explicit affirmative. A missing or malformed
    signal must not block an otherwise-groundable ask — the readiness axis
    already fails closed on its own, and defaulting to stale would refuse every
    issue the moment the model's output drifted."""
    verdict = validate(_output(stale=raw))

    assert verdict.stale is False
    assert verdict.ready is True


@pytest.mark.parametrize("raw", [True, "true", "yes", "TRUE"])
def test_explicit_affirmative_staleness_refuses(raw):
    verdict = validate(_output(stale=raw))

    assert verdict.stale is True
    assert verdict.ready is False


def test_staleness_is_a_grading_time_axis_and_adds_no_cognition_call():
    """FR-008: the axis rides the SAME one-shot call — the zero-token idle
    guarantee is untouched because nothing new is invoked on a tick path."""
    calls = []

    async def fake_caller(prompt):
        calls.append(prompt)
        return '{"ready": false, "stale": true, "missing": [], "rationale": "done"}'

    import asyncio

    verdict = asyncio.run(
        intake_readiness.evaluate(
            what="add the thing",
            done_when="the thing exists",
            context=None,
            repo_context="the thing already exists at src/thing.py",
            claude_caller=fake_caller,
        )
    )

    assert len(calls) == 1, "staleness must not add a second cognition call"
    assert verdict.stale is True and verdict.ready is False


def test_prompt_asks_the_staleness_question_and_declares_the_output_field():
    """The parser is useless if the prompt never asks. Assert both presence in
    the rendered prompt AND that the axis is declared in the output schema."""
    prompt = intake_readiness.build_prompt(
        what="add the thing",
        done_when="the thing exists",
        repo_context="src/thing.py",
    )

    assert "staleness check" in prompt.lower()
    assert '"stale": true | false' in prompt


# ---- contract-section axis (issue #769) ------------------------------------


def test_body_unparseable_by_done_gate_cannot_grade_ready():
    """A body that extract_acceptance cannot parse cannot earn devclaw-ready —
    grading uses the same parser as the done-gate (ONE definition), and the
    refusal fires pre-cognition so no LLM session is wasted.

    This pins the class-level fail-closed invariant introduced by #769:
    bold acceptance text such as '**Acceptance criteria**' is not a recognized
    contract section and must block grading before the LLM is invoked."""
    llm_calls: list[str] = []
    labels_added: list[str] = []
    comments_posted: list[str] = []

    async def fake_caller(prompt: str) -> str:
        llm_calls.append(prompt)
        # Would award ready if reached — proves the check fires before this.
        return '{"ready": true, "missing": [], "rationale": "looks good", "stale": false}'

    # Bold acceptance text — prose but not a heading: extract_acceptance returns None.
    body_bold_criteria = (
        "## What\n\n"
        "Add the ledger reconciliation check.\n\n"
        "**Acceptance criteria**\n\n"
        "- The reconciler runs nightly.\n"
        "- Failures page the on-call rotation.\n"
    )

    class _Gh:
        async def view_issue(self, repo: str, issue: str):
            return {"body": body_bold_criteria, "state": "OPEN", "title": "Add reconciler"}

        async def ensure_label(self, repo: str, name: str) -> None:
            pass

        async def add_labels(self, repo: str, issue: str, labels: list) -> None:
            labels_added.extend(labels)

        async def remove_labels(self, repo: str, issue: str, labels: list) -> None:
            pass

        async def comment(self, repo: str, issue: str, body: str) -> None:
            comments_posted.append(body)

    class _Project:
        id = "test-proj"
        repo_url = "https://github.com/owner/repo"
        workspace_dir = ""

    class _Registry:
        def get(self, project_id: str):
            return _Project()

    result = asyncio.run(
        intake.regrade(
            _Registry(),
            project_id="test-proj",
            issue="https://github.com/owner/repo/issues/42",
            claude_caller=fake_caller,
            gh=_Gh(),
        )
    )

    assert result["readiness"] == NEEDS_REFINEMENT_LABEL, (
        "a body the done-gate cannot parse must not earn devclaw-ready"
    )
    assert len(llm_calls) == 0, (
        "the contract-section check must fire before any LLM call"
    )
    assert NEEDS_REFINEMENT_LABEL in labels_added, (
        "needs-refinement label must be applied"
    )
    # The posted comment must name the accepted heading forms so a human can fix
    # the issue without reading devclaw source.
    assert comments_posted, "a comment explaining the refusal must be posted"
    full_comment = "\n".join(comments_posted)
    assert "Done when" in full_comment or "Acceptance" in full_comment, (
        "the refusal comment must name the accepted heading forms"
    )


def test_no_contract_reason_names_accepted_heading_forms():
    """The static reason string (ONE home — imported by the test) names both
    accepted forms so neither drift silently."""
    assert '"## Done when"' in NO_CONTRACT_REASON or "## Done when" in NO_CONTRACT_REASON
    assert '"## Acceptance' in NO_CONTRACT_REASON or "## Acceptance" in NO_CONTRACT_REASON
    # Must NOT say the issue is already resolved — that is the staleness message.
    assert "already" not in NO_CONTRACT_REASON.lower()
