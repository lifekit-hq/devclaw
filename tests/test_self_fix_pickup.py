"""Self-issue-filing Stage 2 (P2 — FIX pickup) — named regression tests.

Each pins one property the pickup exists to guarantee: pick up ONLY human-
``accepted`` issues from the two sanctioned intakes — ``devclaw:self-filed`` and
the human-handoff ``devclaw:pickup`` marker (O5 amendment, 2026-07-28) — open
exactly one ``one_shot`` self-fix goal per issue and claim it with
``devclaw:fixing``, honour the concurrency cap ACROSS both intakes, dedupe an
issue carrying both markers, self-heal a re-pick idempotently
(``FileExistsError``), and — the zero-token / no-egress guard — do nothing at
all when the self-repo isn't configured. NO auto-merge is exercised
anywhere: P2 opens a PR a human merges (proposal §5A). The GitHub side and goal
creation are both fakes, so tests never shell out and never touch the goal store.
See ``devclaw/goal/self_issue.py`` (Stage 2 section) + the wiring in
``goal/service.py`` (asserted in ``test_cycle_report.py``).
"""

from __future__ import annotations

import asyncio

from devclaw.goal import self_issue as si


# ---- fakes ------------------------------------------------------------------

class FakeGh:
    """Records the two Stage-2 calls; returns a canned issue list."""

    def __init__(self, issues=None, by_labels=None, prs_by_issue=None):
        self._issues = issues or []
        self._by_labels = by_labels  # optional {tuple(labels): [issues]} per-query map
        self._prs_by_issue = prs_by_issue or {}  # {issue_number: [pr_numbers]}
        self.listed: list = []
        self.marked: list = []
        self.pr_checks: list = []  # (repo, number) pairs queried

    async def list_issues(self, repo, *, labels, state="open"):
        self.listed.append((repo, tuple(labels), state))
        if self._by_labels is not None:
            return list(self._by_labels.get(tuple(labels), []))
        return list(self._issues)

    async def mark_fixing(self, repo, number, *, label, comment):
        self.marked.append((number, label))
        return True

    async def open_prs_for_issue(self, repo, number):
        self.pr_checks.append((repo, number))
        return list(self._prs_by_issue.get(number, []))


class SpyCreate:
    """Stand-in for ``GoalService.create_goal`` — captures kwargs; can raise
    ``FileExistsError`` for chosen ids to exercise the idempotent re-claim path."""

    def __init__(self, raise_exists=()):
        self.calls: list = []
        self._raise = set(raise_exists)

    def __call__(self, goal_id, **kw):
        self.calls.append((goal_id, kw))
        if goal_id in self._raise:
            raise FileExistsError(goal_id)
        return {"id": goal_id}


def _issue(number, *, title="a bug", body="", accepted=True, self_filed=True,
           pickup=False, fixing=False):
    labels = []
    if accepted:
        labels.append({"name": si.ACCEPTED_LABEL})
    if self_filed:
        labels.append({"name": si.SELF_FILED_LABEL})
    if pickup:
        labels.append({"name": si.PICKUP_LABEL})
    if fixing:
        labels.append({"name": si.FIXING_LABEL})
    return {"number": number, "title": title, "body": body, "labels": labels}


# ---- pure selection ---------------------------------------------------------

def test_select_for_pickup_respects_concurrency_and_fixing_label():
    issues = [_issue(1, fixing=True), _issue(2), _issue(3)]
    # one in-flight, concurrency 1 → budget 0 → nothing.
    assert si.select_for_pickup(issues, concurrency=1) == []
    # concurrency 2 → budget 1 → the first fresh issue only.
    assert [i["number"] for i in si.select_for_pickup(issues, concurrency=2)] == [2]
    # concurrency 3 → budget 2 → both fresh, in list order.
    assert [i["number"] for i in si.select_for_pickup(issues, concurrency=3)] == [2, 3]


def test_self_fix_workspace_honours_container_prefix(monkeypatch):
    monkeypatch.setenv("DEVCLAW_CONTAINER_PATH_PREFIX", "/var/lib/devclaw/workspaces")
    assert si.self_fix_workspace("self-fix-issue-9") == "/var/lib/devclaw/workspaces/self-fix-issue-9"
    monkeypatch.delenv("DEVCLAW_CONTAINER_PATH_PREFIX", raising=False)
    assert si.self_fix_workspace("self-fix-issue-9") == "/repos/self-fix-issue-9"


# ---- orchestration (fake gh + fake create_goal) -----------------------------

def test_pickup_spawns_one_shot_goal_and_claims_the_issue():
    gh = FakeGh([_issue(42, title="gate crashes on big diff", body="stack trace here")])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    assert len(spy.calls) == 1
    gid, kw = spy.calls[0]
    assert gid == "self-fix-issue-42"
    assert kw["mode"] == "one_shot"            # bounded single-issue fix (ADR 0003 dial)
    assert kw["open_pr"] is True               # opens a PR — a human merges it
    assert kw["repo_url"] == "https://github.com/lifekit-hq/devclaw.git"  # URL, not slug
    assert kw["workspace_dir"].endswith("/self-fix-issue-42")
    assert "#42" in kw["objective"]
    # claimed on GitHub so concurrency accounting + visibility hold across restarts.
    assert gh.marked == [(42, si.FIXING_LABEL)]
    assert res.picked == [(42, "self-fix-issue-42")]


def test_generated_self_fix_params_pass_goal_admission():
    """The params the pickup synthesises must clear the real admission gate — else a
    self-fix goal would be rejected at create_goal. Pins objective/done_when/workspace
    against a future admission tightening."""
    from devclaw.goal.admission import verify_goal

    issue = _issue(99, title="planner drops repo context", body="")
    adm = verify_goal(
        objective=si.self_fix_objective(issue, "lifekit-hq/devclaw"),
        workspace_dir=si.self_fix_workspace("self-fix-issue-99"),
        done_when=si.self_fix_done_when(99, "lifekit-hq/devclaw"),
        repo_url=si.self_repo_url("lifekit-hq/devclaw"),
        backlog=None, verify_cmd=None, spec="",
        # Spec 012 US2: this creator is UNATTENDED, so it must fill the saga
        # slots for real rather than leaning on an operator to catch a
        # sprawling prompt. Pinned against the same admission gate.
        out_of_scope=si.self_fix_out_of_scope(99, "lifekit-hq/devclaw"),
        invariants=list(si.SELF_FIX_INVARIANTS),
        established=si.self_fix_established(99, "lifekit-hq/devclaw"),
    )
    assert adm.admitted


def test_pickup_concurrency_one_blocks_when_one_already_fixing():
    gh = FakeGh([_issue(1, fixing=True), _issue(2)])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh, concurrency=1))
    assert spy.calls == []                     # budget full — nothing new spawned
    assert res.picked == []


def test_pickup_reclaims_existing_goal_on_filexists_without_error():
    gh = FakeGh([_issue(7)])
    spy = SpyCreate(raise_exists={"self-fix-issue-7"})
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))
    assert len(spy.calls) == 1                  # attempted (idempotent create)
    assert gh.marked == [(7, si.FIXING_LABEL)]  # still claimed — self-heal, not error
    assert res.picked == [(7, "self-fix-issue-7")]


def test_no_op_and_no_egress_when_self_repo_unset(monkeypatch):
    monkeypatch.delenv("DEVCLAW_SELF_REPO", raising=False)
    gh = FakeGh([_issue(1)])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, gh=gh))  # no repo passed → env gate
    assert spy.calls == [] and gh.listed == [] and res.picked == []


def test_pickup_gh_list_failure_is_swallowed():
    class BoomGh(FakeGh):
        async def list_issues(self, repo, *, labels, state="open"):
            raise RuntimeError("gh exploded")

    gh = BoomGh()
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))
    assert spy.calls == [] and res.picked == []  # logged + swallowed, edge intact


# ---- the O5 amendment: human-handoff intake (devclaw:pickup) ----------------

def _both_intakes(self_filed_issues, handoff_issues):
    return FakeGh(by_labels={
        (si.ACCEPTED_LABEL, si.SELF_FILED_LABEL): self_filed_issues,
        (si.ACCEPTED_LABEL, si.PICKUP_LABEL): handoff_issues,
    })


def test_pickup_accepts_human_filed_issue_with_pickup_label():
    """A human-filed issue armed with accepted + devclaw:pickup is picked up
    exactly like a self-filed one (O5 amendment, 2026-07-28)."""
    gh = _both_intakes([], [_issue(401, title="deploy launcher gap",
                                   self_filed=False, pickup=True)])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    assert [g for _, g in res.picked] == ["self-fix-issue-401"]
    assert gh.marked == [(401, si.FIXING_LABEL)]
    # both intakes were queried, self-filed first.
    assert [labels for _, labels, _ in gh.listed] == [
        (si.ACCEPTED_LABEL, si.SELF_FILED_LABEL),
        (si.ACCEPTED_LABEL, si.PICKUP_LABEL),
    ]


def test_pickup_dedupes_issue_carrying_both_markers():
    """An issue labelled self-filed AND pickup appears in both queries but must
    spawn exactly one goal and one claim."""
    both = _issue(7, pickup=True)
    gh = _both_intakes([both], [both])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    assert res.picked == [(7, "self-fix-issue-7")]
    assert len(spy.calls) == 1
    assert gh.marked == [(7, si.FIXING_LABEL)]


def test_pickup_concurrency_shared_across_intakes():
    """One in-flight self-filed fix consumes the whole concurrency-1 budget —
    a fresh handoff issue must NOT be picked this cycle (serialize
    self-modification, proposal 5A; the cap is global, not per-intake)."""
    gh = _both_intakes(
        [_issue(1, fixing=True)],
        [_issue(2, self_filed=False, pickup=True)],
    )
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    assert res.picked == []
    assert spy.calls == []
    assert gh.marked == []


# ---- #576: in-review issues (open linked PR) --------------------------------

def test_grade_backlog_marks_in_review_when_open_pr():
    """grade_backlog stamps in_review=True on any issue with an open linked PR."""
    issues = [_issue(1), _issue(2), _issue(3)]
    graded = si.grade_backlog(issues, open_prs_by_number={2: [101]})
    assert graded[0]["in_review"] is False   # #1 — no open PR
    assert graded[1]["in_review"] is True    # #2 — PR #101 open
    assert graded[2]["in_review"] is False   # #3 — not in lookup → ready


def test_grade_backlog_does_not_mutate_input():
    """grade_backlog returns new dicts; the input issues are not modified."""
    issues = [_issue(5)]
    si.grade_backlog(issues, open_prs_by_number={5: [200]})
    assert "in_review" not in issues[0]


def test_select_for_pickup_skips_in_review_issues():
    """select_for_pickup skips issues with in_review=True regardless of the
    concurrency budget — an open PR is the active work item."""
    ready = {**_issue(1), "in_review": False}
    in_review = {**_issue(2), "in_review": True}
    fresh = {**_issue(3), "in_review": False}
    picked = si.select_for_pickup([ready, in_review, fresh], concurrency=3)
    assert [i["number"] for i in picked] == [1, 3]


def test_pickup_skips_issue_with_open_linked_pr(monkeypatch):
    """run_self_fix_pickup does not dispatch a goal for an issue that already has an
    open linked PR — the grader marks it in_review and select_for_pickup skips it.
    Closes #576."""
    gh = FakeGh(
        [_issue(10, title="a bug"), _issue(11, title="another bug")],
        prs_by_issue={10: [55]},   # issue #10 has open PR #55 — in review
    )
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    # Only issue #11 is dispatched — #10 is in review.
    assert [n for n, _ in res.picked] == [11]
    assert len(spy.calls) == 1
    assert spy.calls[0][0] == "self-fix-issue-11"
    assert gh.marked == [(11, si.FIXING_LABEL)]
    # The grader queried open PRs for all issues.
    assert set(n for _, n in gh.pr_checks) == {10, 11}


def test_pickup_open_prs_infra_failure_degrades_to_ready(monkeypatch):
    """When open_prs_for_issue raises (gh infra down), the issue is treated as
    having no open PRs and is picked up normally — fail-open on infra uncertainty."""
    class BoomPrGh(FakeGh):
        async def open_prs_for_issue(self, repo, number):
            raise RuntimeError("gh timed out")

    gh = BoomPrGh([_issue(20, title="boom pr check")])
    spy = SpyCreate()
    res = asyncio.run(si.run_self_fix_pickup(spy, repo="lifekit-hq/devclaw", gh=gh))

    # Infra hiccup → no open PRs assumed → issue picked up.
    assert [n for n, _ in res.picked] == [20]
