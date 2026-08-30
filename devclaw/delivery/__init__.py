"""Deliver a completed task's change as a reviewable branch + PR.

After a task passes its gate chain, the agent's change has already been
MATERIALIZED as a commit (spec 013 — `devclaw/task_change.py`), and that commit
is the object the gates judged. Delivery turns it into something you *review*
instead of *produce*: a branch, a push, and — if the remote is GitHub and ``gh``
is authed — a pull request whose URL is recorded on the task. It publishes the
judged object; it does not work out what changed. (The self-discovering path
is still here for callers that pass no ``judged_head``.)

Design:
  * **Best-effort + non-fatal.** A delivery failure never un-does a ``done`` task;
    it records what it managed (``branch`` / ``pushed`` / ``pr_url`` / ``error``).
  * **Graceful degradation.** Not a git repo, or no changes, or no remote, or no
    auth → it does as much as it can (often: commit to a local branch) and stops.
  * **Auth** is a GitHub token (``GITHUB_TOKEN`` / ``GH_TOKEN``) or ``gh``'s own
    login — this is *repo push access*, separate from the Claude OAuth pillar
    (which is about cognition billing, not git).
"""

from __future__ import annotations

import re

from ..advance_brief import is_advance_brief
from ..git_identity import git_identity_env
from ..procutil import run as _run
from ..task_change import MACHINE_COMMIT_SUBJECT


# conventional-commit type per task kind — so a delivered PR reads `feat: …` /
# `fix: …` instead of a raw goal string dumped into the title.
_KIND_TYPE = {
    "implement_feature": "feat",
    "fix_bug": "fix",
    "review_repository": "chore",
    "onboard": "docs",
}


def _slug(text: str, n: int = 40) -> str:
    """Branch-safe slug, truncated on a word boundary (no mid-word cuts like
    `…-endpoint-that-ret`)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > n:
        s = s[:n].rsplit("-", 1)[0]  # drop the partial trailing segment
    return s.strip("-") or "change"


def _clean_summary(goal: str) -> str:
    """First line of the goal, stripped of markdown backticks and collapsed
    whitespace — the basis for a human-readable title."""
    first = goal.strip().splitlines()[0] if goal.strip() else "devclaw change"
    return re.sub(r"\s+", " ", first.replace("`", "")).strip() or "devclaw change"


def _truncate_words(s: str, limit: int) -> str:
    """Truncate to `limit` chars on a word boundary, adding an ellipsis if cut."""
    if len(s) <= limit:
        return s
    head = s[:limit]
    if " " in head:
        head = head.rsplit(" ", 1)[0]  # drop the partial trailing word
    return head.rstrip(" ,.;:-") + "…"


# The intake-pointer preamble an intake-filed objective starts with
# (``Implement intake issue #N of <owner>/<repo>: <what>``). The pointer is a
# ticket reference, not a description — it belongs in the PR body's
# ``Closes #N``, never in a title, where it eats the 72-char budget and pushes
# the actual change description off the end.
_INTAKE_POINTER_RE = re.compile(
    r"\bimplement\s+(?:the\s+)?(?:intake\s+)?issue\s+#(\d+)\s+of\s+[\w.-]+/[\w.-]+\s*:\s*",
    re.IGNORECASE,
)


def _strip_issue_pointer(summary: str) -> str:
    """The summary with a leading intake-pointer preamble removed — the actual
    change description. Unchanged when there's no pointer (or nothing after it)."""
    m = _INTAKE_POINTER_RE.match(summary)
    if not m:
        return summary
    rest = summary[m.end():].strip()
    return rest or summary


def _pr_title(goal: str, kind: str | None = None, limit: int = 72) -> str:
    """A clean, conventional-commit-style title (e.g. `feat: add a /health
    endpoint`) — not the raw goal truncated mid-word."""
    summary = _strip_issue_pointer(_clean_summary(goal))
    prefix = _KIND_TYPE.get(kind or "", "")
    if prefix:
        return f"{prefix}: {_truncate_words(summary, limit - len(prefix) - 2)}"
    return _truncate_words(summary, limit)


# A conventional-commit subject: `type(scope)?!: summary`.
_CC = re.compile(r"^([a-z]+)(\([^)]+\))?!?:\s*(.+)$", re.IGNORECASE)


def _cc_type(subject: str, kind: str | None) -> str:
    """The conventional-commit *type* for a branch prefix — taken from the agent's
    own commit subject if it wrote one (`fix(x): …` → `fix`), else mapped from the
    task kind. So the branch matches what the change actually is."""
    m = _CC.match(subject.strip())
    return (m.group(1).lower() if m else "") or _KIND_TYPE.get(kind or "", "chore")


def _cc_description(subject: str) -> str:
    """The subject with any leading `type(scope): ` stripped — the basis for a
    clean branch slug (`feat/add-deal-crud`, not `feat/feat-add-deal-crud`)."""
    m = _CC.match(subject.strip())
    return (m.group(3) if m else subject).strip()


def _looks_conventional(subject: str) -> bool:
    return bool(_CC.match(subject.strip()))


# Issue references the delivered change resolves. GitHub links AND auto-closes an
# issue when a PR body says ``Closes #N``, so we surface any issue the goal or the
# engineer's own commit named — the professional equivalent of a linked ticket.
# Conservative on purpose: only explicit fix/close verbs, the self-fix
# ``issue #N`` objective, and the intake-pointer objective count, so a passing
# mention (``see #99 for context``) never triggers a spurious auto-close.
_CLOSES_RE = re.compile(
    r"\b(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\s+#(\d+)", re.IGNORECASE
)
# The self-fix objective shape (``Fix devclaw issue #N: …``) — anchored on a
# fix-verb so an incidental "see issue #50 for context" never auto-closes #50.
_ISSUE_RE = re.compile(r"\bfix(?:es|ed)?\s+\S+\s+issue\s+#(\d+)", re.IGNORECASE)


def _closes_issues(*texts: str | None) -> list[int]:
    """Issue numbers this change resolves, pulled (deduped, in first-seen order)
    from the goal text and the engineer's own commit body."""
    seen: list[int] = []
    for text in texts:
        if not text:
            continue
        for pattern in (_CLOSES_RE, _ISSUE_RE, _INTAKE_POINTER_RE):
            for m in pattern.finditer(text):
                n = int(m.group(1))
                if n not in seen:
                    seen.append(n)
    return seen


# A standalone close/fix directive line (``Fixes #42``) — stripped from the lead
# prose so the issue link renders once, canonically, as the body's Closes section.
_DIRECTIVE_LINE = re.compile(
    r"\s*(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\s+#\d+\s*$", re.IGNORECASE
)


def _strip_directive_lines(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not _DIRECTIVE_LINE.match(ln)
    ).strip()


async def _agent_commit_msg(workspace_dir: str, base: str | None) -> tuple[str, str] | None:
    """The (subject, body) the AGENT committed for this change (HEAD of base..HEAD),
    or None if it didn't commit. The engineer writing its own commit is what makes
    the delivered PR describe WHAT CHANGED instead of pasting the task instruction."""
    rng = f"{base}..HEAD" if base else "HEAD~1..HEAD"
    rc, subj = await _run("git", "log", "-1", "--format=%s", rng, cwd=workspace_dir)
    if rc != 0 or not subj.strip():
        return None
    _, body = await _run("git", "log", "-1", "--format=%b", rng, cwd=workspace_dir)
    return subj.strip(), body.strip()


def _is_advance_brief(goal: str) -> bool:
    """The thin-advance pull-brief (``goal/tick.py:_advance_brief``) is generic
    plumbing — "Advance this goal by one substantive, shippable increment…" — not
    a description of any change. Post-demolition it's the task ``goal`` on every
    long_lived tick, so it must NEVER leak into a PR title. Detection lives in
    :mod:`devclaw.advance_brief` (shared with the display half, #550) so the
    generator and every detector stay in lockstep."""
    return is_advance_brief(goal)


def _link_title_branch(title: str, branch: str, issues: list[int]) -> tuple[str, str]:
    """Fold a resolved issue reference into the PR title and branch — the
    linked-ticket parallel. The title gets a trailing ``(#N)`` (skipped if it
    already names the issue or would exceed the 72-char budget); the branch slug
    is prefixed with the issue number (``fix/42-…``). Only the FIRST issue
    decorates title/branch — the full set still renders as ``Closes #N`` lines in
    the PR body."""
    if not issues:
        return title, branch
    n = issues[0]
    if f"#{n}" not in title:
        candidate = f"{title} (#{n})"
        if len(candidate) <= 72:
            title = candidate
    if "/" in branch:
        typ, _, slug = branch.partition("/")
        if slug != str(n) and not slug.startswith(f"{n}-"):
            branch = f"{typ}/{n}-{slug}"
    return title, branch


def _resolve_title(
    *,
    planner_title: str | None,
    agent_msg: tuple[str, str] | None,
    goal: str,
    kind: str | None,
    task_id: str,
) -> tuple[str, str, str | None]:
    """Choose the PR ``(title, derived_branch, changes)`` from the best available
    source, in priority order: the planner's explicit title → the ENGINEER's own
    commit subject (whenever it committed — see the ``ahead > 0`` note at the call
    site: a stray uncommitted file must not sink the worker-authored subject into
    the generic brief) → a goal-derived heuristic that NEVER renders the
    thin-advance brief (falling back to its embedded objective instead). A
    resolved issue reference (self-fix ``issue #N`` objective, or the engineer's
    own ``Fixes #N``) decorates the title + branch before returning."""
    planner = (planner_title or "").strip() or None
    if planner:
        prefixed = planner if _looks_conventional(planner) else _pr_title(planner, kind)
        title = _truncate_words(prefixed, 72)
        branch = f"{_cc_type(title, kind)}/{_slug(_cc_description(title))}"
        changes = (agent_msg[1] or agent_msg[0]) if agent_msg else None
        title, branch = _link_title_branch(title, branch, _closes_issues(goal, changes, planner))
        return title, branch, changes
    if agent_msg:
        subject, body = agent_msg
        title = _truncate_words(subject if _looks_conventional(subject) else _pr_title(subject, kind), 72)
        branch = f"{_cc_type(subject, kind)}/{_slug(_cc_description(subject))}"
        title, branch = _link_title_branch(title, branch, _closes_issues(goal, body, subject))
        return title, branch, body or subject
    # No worker commit and no planner title — the dispatch prompt is NOT a
    # source for the PR title (criterion 2). Use the fixed machine-commit
    # subject so the PR title matches the actual commit devclaw will write,
    # and so no instruction text from the ask can ever appear here.
    branch = f"devclaw/{task_id[:8]}-snapshot"
    title = MACHINE_COMMIT_SUBJECT
    title, branch = _link_title_branch(title, branch, _closes_issues(goal))
    return title, branch, None


#: Constant provenance label attached to every devclaw-delivered PR — the
#: machine-readable "shipped by devclaw" marker external reporting filters on
#: (the PR-body signature is prose; a label is the first-class GitHub filter).
PROVENANCE_LABEL = "devclaw"


def _scope_label(title: str) -> str:
    """A light PR label from the conventional-commit scope (``feat(tags):`` →
    ``tags``) or, absent a scope, the type (``fix:`` → ``fix``) — so a delivered PR
    carries an area/kind label the way a human-managed repo does. Empty when the
    title isn't conventional (no label rather than a junk one)."""
    m = _CC.match(title.strip())
    if not m:
        return ""
    scope = (m.group(2) or "").strip("()").strip()
    return _slug(scope or m.group(1), n=30)


async def _apply_pr_labels(workspace_dir: str, pr_url: str, title: str) -> None:
    """Best-effort: create-if-missing + attach the constant ``devclaw``
    provenance label plus a scope/kind label to the PR. Labels are cosmetic —
    every step is non-fatal (``_run`` never raises), so a labelling failure
    can't fail a delivered PR."""
    labels = [PROVENANCE_LABEL]
    scope = _scope_label(title)
    if scope and scope != PROVENANCE_LABEL:
        labels.append(scope)
    for label in labels:
        await _run("gh", "label", "create", label, "--color", "ededed", "--force", cwd=workspace_dir)
    await _run("gh", "pr", "edit", pr_url, "--add-label", ",".join(labels), cwd=workspace_dir)


# A `Co-Authored-By:` trailer — belongs in the COMMIT (where the worker model
# stays visible as co-author), NOT duplicated into the PR description body.
_COAUTHOR_LINE = re.compile(r"\s*Co-Authored-By:\s*.+$", re.IGNORECASE)

#: PR body lead used when the agent committed nothing — explicit about the
#: absence rather than silently substituting the dispatch prompt (criterion 3).
_NO_AGENT_COMMIT_LEAD = (
    "_Agent authored no commit for this change — the workspace was captured "
    "as a machine snapshot. See the task run log for details._"
)


def _strip_coauthor_lines(text: str) -> str:
    """Drop ``Co-Authored-By:`` trailer lines from a PR-body lead."""
    return "\n".join(
        ln for ln in text.splitlines() if not _COAUTHOR_LINE.match(ln)
    ).strip()


#: The plain generated-by signature — matches the Claude-Code house PR style
#: (the constant `devclaw` provenance LABEL, #510, carries the "who", so the body
#: footer stays clean instead of leaking a task UUID).
_PR_SIGNATURE = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


def _pr_body(
    goal: str, task_id: str, verify: dict | None,
    *, changes: str | None = None, advisories: list | None = None,
) -> str:
    """A clean, Claude-Code-style PR body: a ``## Summary`` of what changed, a
    ``## Testing`` note, ``Closes #N`` for any resolved issue, any trust-mode
    advisory, and a plain generated-by signature. No original-task dump, no
    telemetry, no leaked ``Co-Authored-By`` trailer (that stays in the commit).

    ``advisories`` (ADR 0007): trust-mode dial-able gate findings this change
    SHIPPED past rather than blocked on. Rendered as a loud section so the human
    sees them at the merge boundary — the backstop for advisory gates."""
    lead = (
        _strip_directive_lines(changes)
        if changes is not None
        else _NO_AGENT_COMMIT_LEAD
    )
    lead = _strip_coauthor_lines(lead)
    parts = ["## Summary", "", lead or "(see commit)", ""]
    if verify and verify.get("ran"):
        cmd = verify.get("cmd", "")
        parts += ["## Testing", ""]
        if verify.get("passed"):
            parts += [f"- Verified with `{cmd}` — passing.", ""]
        else:
            parts += [f"- Gate `{cmd}` did **not** pass — see the task error.", ""]
    for n in _closes_issues(goal, changes):
        parts += [f"Closes #{n}"]
    if _closes_issues(goal, changes):
        parts += [""]
    if advisories:
        parts += ["## ⚠️ Advisory — shipped under `trust`, review before merging"]
        parts += [
            "These gates flagged an issue but did not block delivery (the goal's "
            "strictness dial is `trust`, ADR 0007). The merge is the enforcement "
            "point — read these before approving:",
            "",
        ]
        for a in advisories:
            gate = (a.get("gate") if isinstance(a, dict) else None) or "gate"
            reason = (a.get("reason") if isinstance(a, dict) else str(a)) or ""
            parts += [f"- **{gate}**: {reason.strip()}"]
        parts += [""]
    parts += [_PR_SIGNATURE]
    return "\n".join(parts)


def _goal_pr_body(
    goal: str, task_id: str, verify: dict | None,
    subjects: list[str], *, changes: str | None = None, advisories: list | None = None,
) -> str:
    """The PR body for a GOAL-branch PR (one PR spans the whole goal): a lead
    derived from the agent's commit body (never the dispatch prompt), the running
    list of increments landed on the branch, a verify note for the latest
    increment, ``Closes #N``, any trust-mode advisories, and a plain signature.
    Refreshed on every delivery so the reviewer always sees the accumulated state.

    ``changes`` is the agent's commit body for the latest increment — the same
    source ``_pr_body`` uses. When None, renders ``_NO_AGENT_COMMIT_LEAD`` rather
    than echoing the dispatch prompt (goal text is NEVER the lead)."""
    lead = (
        _strip_directive_lines(changes)
        if changes is not None
        else _NO_AGENT_COMMIT_LEAD
    )
    lead = _strip_coauthor_lines(lead)
    parts = [lead or "(see commit)", ""]
    if subjects:
        parts += [f"## Increments landed on this goal branch ({len(subjects)})"]
        parts += [f"- {s}" for s in subjects]
        parts += [""]
    if verify and verify.get("ran"):
        cmd = verify.get("cmd", "")
        if verify.get("passed"):
            parts += [f"Latest increment verified with `{cmd}` — passing.", ""]
        else:
            parts += [f"Latest gate `{cmd}` did **not** pass — see the task error.", ""]
    for n in _closes_issues(goal, changes):
        parts += [f"Closes #{n}"]
    if _closes_issues(goal, changes):
        parts += [""]
    if advisories:
        parts += ["## ⚠️ Advisory — shipped under `trust`, review before merging"]
        for a in advisories:
            gate = (a.get("gate") if isinstance(a, dict) else None) or "gate"
            reason = (a.get("reason") if isinstance(a, dict) else str(a)) or ""
            parts += [f"- **{gate}**: {reason.strip()}"]
        parts += [""]
    parts += [_PR_SIGNATURE]
    return "\n".join(parts)



def _extract_pr_url(text: str) -> str | None:
    m = re.search(r"https://github\.com/\S+/pull/\d+", text)
    return m.group(0) if m else None


async def _current_branch(workspace_dir: str) -> str | None:
    """The workspace's currently-checked-out branch, or None on detached HEAD /
    non-repo. Used to detect goal-branch mode: prepare_workspace(branch=...)
    puts the workspace on a ``goal/<goal_id>`` branch, and that's how delivery
    knows to PUSH to that branch (and reuse the goal's single PR) rather than
    creating a new task-scoped branch."""
    rc, out = await _run("git", "branch", "--show-current", cwd=workspace_dir)
    if rc != 0:
        return None
    name = out.strip()
    return name or None


async def _head_is_pushed(workspace_dir: str) -> bool:
    """True iff HEAD is already contained in some remote-tracking branch — i.e.
    it has been pushed and amending it would rewrite published history (a
    force-push). False for a fresh local commit (nothing on a remote contains it
    yet), which is the safe-to-amend case. Branch-agnostic on purpose: it asks
    'is this commit published anywhere' rather than trusting a specific
    ``origin/<branch>`` ref to be freshly fetched."""
    rc, out = await _run("git", "branch", "-r", "--contains", "HEAD", cwd=workspace_dir)
    return rc == 0 and bool(out.strip())


async def _recent_commit_subjects(workspace_dir: str, base: str | None) -> list[str]:
    """The subjects of the commits this branch carries beyond its base
    (``base..HEAD``), oldest first — the running list of increments that have
    landed on a goal branch, for the goal PR body. Best-effort: [] if the range
    can't be resolved."""
    if not base:
        return []
    rc, out = await _run(
        "git", "log", "--reverse", "--format=%s", f"{base}..HEAD", cwd=workspace_dir,
    )
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


async def _find_pr_for_branch(workspace_dir: str, branch: str) -> str | None:
    """The url of an existing open PR with ``--head <branch>``, or None.
    Used when delivering to a goal branch so the second + Nth item just push
    new commits to the same branch (the PR auto-updates) instead of trying
    to ``gh pr create`` over an existing PR (which fails)."""
    rc, out = await _run(
        "gh", "pr", "list", "--head", branch, "--state", "open",
        "--json", "url", "--jq", ".[0].url // empty",
        cwd=workspace_dir,
    )
    if rc != 0:
        return None
    return out.strip() or None


async def _default_base_ref(workspace_dir: str, base_branch: str | None = None) -> str | None:
    """The base ref for the ahead-count/diff range as a local ref (e.g.
    'origin/main'), or None if there's no usable ref. Used to tell whether the
    agent committed its change to a branch (HEAD ahead of base).

    ``base_branch`` (the v1-helper-resurface delivery seam) is a caller-chosen
    PR base: when set, its origin tracking ref (or, failing that, the local
    branch) wins; when unset — or when the chosen base doesn't resolve in this
    workspace — behavior falls through to the remote-default lookup, exactly
    today's ``origin/HEAD`` → ``main`` → ``master`` chain."""
    if base_branch:
        for cand in (f"origin/{base_branch}", base_branch):
            rc, _ = await _run("git", "rev-parse", "--verify", "--quiet", cand, cwd=workspace_dir)
            if rc == 0:
                return cand
    rc, out = await _run(
        "git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", cwd=workspace_dir
    )
    if rc == 0 and out.strip().startswith("refs/remotes/"):
        return out.strip()[len("refs/remotes/") :]
    for cand in ("origin/main", "origin/master"):
        rc, _ = await _run("git", "rev-parse", "--verify", "--quiet", cand, cwd=workspace_dir)
        if rc == 0:
            return cand
    return None


#: errors that mean "there was nothing to ship" or "shipping stopped by design"
#: (a local-only repo has no remote to push to — the local branch IS the
#: deliverable), as opposed to "shipping was attempted and broke".
_BENIGN_ERRORS = ("no changes to deliver", "no 'origin' remote")


def delivery_failed(result: dict) -> str | None:
    """The failure message when a delivery ATTEMPT broke (branch/commit/push/
    PR-create), else None. Benign no-op outcomes — nothing to ship, local-only
    repo — are not failures: a task carrying one still settles ``done``, just
    without a PR. Everything else means a verified change exists but never
    became the reviewable artifact the caller asked for, and the task must NOT
    settle ``done`` (a done-without-PR row reads as shipped to every poller
    upstream)."""
    err = result.get("error")
    if not err:
        return None
    if any(err.startswith(b) for b in _BENIGN_ERRORS):
        return None
    return err


async def deliver_change(
    *,
    workspace_dir: str,
    task_id: str,
    goal: str,
    kind: str | None = None,
    verify: dict | None = None,
    title: str | None = None,
    advisories: list | None = None,
    base_branch: str | None = None,
    target_branch: str | None = None,
    judged_head: str | None = None,
    agent_authored: bool | None = None,
) -> dict:
    """Commit the workspace's change to a branch and (best-effort) push + open a PR.
    Returns a verdict dict; never raises. ``kind`` shapes the conventional-commit
    title (feat/fix/…); ``verify`` (the gate verdict) goes into the PR body.
    ``title`` is the caller's chosen PR title (the task row's ``title``
    column). When present and non-empty it wins over the
    engineer's own commit subject and the goal-derived heuristic; a resolved
    issue reference then decorates it (title ``(#N)`` + branch) via
    ``_resolve_title``.

    ``base_branch`` / ``target_branch`` are the v1-helper-resurface delivery
    seam (docs/proposals/v1-helper-resurface.md §3): ``base_branch`` is a
    caller-chosen PR base — it becomes the ahead-count/diff base ref and the
    ``gh pr create --base``; ``target_branch`` pins the delivery branch — when
    the workspace is already ON it, delivery stays there and reuses its single
    PR (the same machinery goal branches use, no new reuse logic). Both default
    to None ⇒ the per-task-branch shape (fresh derived branch → the
    remote's default base).

    ``judged_head`` is the post-run reference the gates judged (spec 013): the
    sha of the materialized change. When supplied, delivery **publishes that
    object and performs no discovery of its own** — no reading of the working
    tree for a change, no ``git add -A``, no commit. It verifies that HEAD still
    IS that object and fails LOUD otherwise, because a drifted workspace means
    what would ship is not what was judged. ``agent_authored`` says whether the
    commit at ``judged_head`` was written by the WORKER (so its subject describes
    the change and should title the PR) or by devclaw's materialization step.
    Both default to None ⇒ the self-discovering path, unchanged."""
    result: dict = {"delivered": False, "branch": None, "committed": False,
                    "pushed": False, "pr_url": None, "error": None}

    rc, _ = await _run("git", "rev-parse", "--is-inside-work-tree", cwd=workspace_dir)
    if rc != 0:
        result["error"] = "workspace is not a git repository"
        return result

    rc, status = await _run("git", "status", "--porcelain", cwd=workspace_dir)
    dirty = rc == 0 and bool(status.strip())

    if judged_head:
        # Spec 013 FR-005: publish the artifact that was judged, and only it.
        # This is a VERIFICATION, not a discovery — the answer to "what changed"
        # arrived with the call. A mismatch means the workspace moved between the
        # gate chain and here, so what would ship is not what passed: fail loud
        # rather than quietly shipping the newer thing.
        rc_h, head = await _run("git", "rev-parse", "HEAD", cwd=workspace_dir)
        head = head.strip()
        if rc_h != 0 or head != judged_head or dirty:
            result["error"] = (
                f"workspace drifted from the judged span: expected HEAD "
                f"{judged_head[:12]}, found {(head or '(unreadable)')[:12]}"
                + (" with uncommitted changes" if dirty else "")
                + " — the change that would ship is not the change the gates "
                "judged, so nothing is published"
            )
            return result

    # The agent may have committed its change to its own branch, leaving a CLEAN
    # working tree — that is still a delivery. Detect commits ahead of the base
    # (caller-chosen ``base_branch``, else the remote's default branch) and ship
    # them, rather than reporting "no changes".
    base = await _default_base_ref(workspace_dir, base_branch)
    ahead = 0
    if base:
        rc_a, cnt = await _run(
            "git", "rev-list", "--count", f"{base}..HEAD", cwd=workspace_dir
        )
        if rc_a == 0 and cnt.strip().isdigit():
            ahead = int(cnt.strip())

    if not judged_head and not dirty and ahead == 0:
        # Self-discovering path only. With a judged head the caller
        # already knows there IS a change — it settles a no-change task without
        # calling here at all (spec 013 FR-014) — so re-deciding the question
        # from an ahead-count would be exactly the second computation this
        # change removes. (A local-only repo has no base ref to count against,
        # so the count is 0 even when the change is real.)
        result["error"] = "no changes to deliver"
        return result

    # Detect branch-reuse mode: prepare_workspace(branch=...) put the workspace
    # on its delivery branch BEFORE the agent ran — either a goal branch
    # (``goal/<id>``) or a caller-pinned ``target_branch`` (the v1-helper
    # delivery seam). In that case all commits the agent made are already on
    # that branch — we push it as-is (no new branch), and its single PR is
    # reused across deliveries. Per-task-branch mode (workspace on the default
    # branch, off-goal, and unpinned) creates a fresh branch per delivery.
    current = await _current_branch(workspace_dir)
    goal_mode = bool(
        current
        and (current.startswith("goal/") or (target_branch is not None and current == target_branch))
    )

    # Prefer the ENGINEER's own commit for the title / branch / PR body — so the
    # delivery reads as "what changed", not the task instruction. The _COMMIT_CODA
    # asks the agent to commit; when it did (clean tree, ahead of base) we derive
    # from its commit. The dirty-tree path (agent left it uncommitted) is the
    # fallback: devclaw commits with a goal-derived conventional title on a
    # devclaw/* branch (so an auto-committed change is visibly distinct from an
    # engineer-authored one).
    # Resolve the engineer's own commit whenever it committed ANYTHING ahead of
    # base — NOT only when the tree is pristine. A worker that commits its feature
    # (`feat(tags): …`) but leaves a stray file uncommitted used to fall through to
    # the goal-derived title and dump the generic advance-brief (the two-commit
    # `devclaw/…advance-this-goal` PR bug). The stray remainder is still committed
    # below; the title comes from the worker's subject.
    # Did the WORKER write the commit subject? With a materialized span the
    # answer arrives with the call; without one, fall back to the historical
    # ``ahead > 0`` proxy. The proxy over-reports in goal-branch mode (prior
    # increments this worker never touched also sit ahead of base) — the same
    # class of guessing spec 013 removes elsewhere.
    read_agent_msg = bool(agent_authored) if judged_head else ahead > 0
    agent_msg = await _agent_commit_msg(workspace_dir, base) if read_agent_msg else None
    # Machine-readable signal: the agent committed nothing. Recorded on the result
    # dict so the caller (settle path) can emit a StateStore event — a telemetry
    # surface distinct from the prose in the PR body (criterion 3, spec 017).
    if agent_msg is None:
        result["no_agent_commit"] = True
    title_slot, derived_branch, changes = _resolve_title(
        planner_title=title, agent_msg=agent_msg, goal=goal, kind=kind, task_id=task_id,
    )
    # ``title`` (the function parameter) has now been consumed; ``title_slot`` is
    # the PR-title string the rest of this function uses. Kept as ``title`` in the
    # commit-message path below so we don't churn the message shape.
    title = title_slot

    if goal_mode and current:  # `and current` is a no-op (goal_mode ⇒ current) that narrows the type
        # Stay on the goal branch — every item commits to it cumulatively.
        branch = current
        result["branch"] = branch
    else:
        branch = derived_branch
        result["branch"] = branch
        # Put the change on its branch. `checkout -b` carries HEAD — including
        # the materialized commit, and any commits the agent made — onto the new
        # branch. The branch we were on is left pointing at the same commit
        # locally; that is harmless because ``prepare_workspace`` hard-resets the
        # default branch to ``origin/<default>`` before every dispatch, and only
        # the new branch is ever pushed. A feature
        # slug can repeat across tasks, so on collision disambiguate with a
        # short task suffix.
        rc, out = await _run("git", "checkout", "-b", branch, cwd=workspace_dir)
        if rc != 0:
            branch = f"{branch}-{task_id[:6]}"
            rc, out = await _run("git", "checkout", "-b", branch, cwd=workspace_dir)
            if rc != 0:
                result["error"] = f"branch failed: {out}"
                return result
            result["branch"] = branch

    if judged_head:
        # The commit already exists — materialization wrote it, the gates judged
        # it, and the drift check above proved HEAD still is it. Nothing to
        # stage, nothing to author: this is where the second computation of
        # "what changed" used to live (#630).
        result["committed"] = True
    elif dirty:
        await _run("git", "add", "-A", cwd=workspace_dir)
        # Fold leftover files into the worker's OWN commit when it made one this
        # run (ahead of base) and that commit is still local. A leftover is almost
        # always a generated/tooling artifact of the same change — a lockfile the
        # verify gate's install regenerated after the worker committed, or a
        # scaffold dotfile the worker forgot to stage. A second commit would both
        # duplicate the worker's headline (two commits, same subject) and leave
        # the worker's commit non-atomic (its code won't build without the
        # swept-in lockfile). Amending an unpushed commit is safe; a pushed commit
        # (a prior goal-branch increment) must NEVER be amended — that needs a
        # force-push — so fall back to devclaw's own goal-titled commit there and
        # in the no-worker-commit (ahead == 0) case.
        if ahead > 0 and not await _head_is_pushed(workspace_dir):
            rc, out = await _run(
                "git", "commit", "--amend", "--no-edit", cwd=workspace_dir,
                env_extra=git_identity_env(),
            )
        else:
            # Devclaw authors this commit because the agent left none. The
            # message is self-describing (machine snapshot), never derived from
            # the dispatch prompt — criterion 4 (spec 017).
            msg = (
                f"{MACHINE_COMMIT_SUBJECT}\n\n"
                f"Delivered by devclaw (task {task_id}). "
                "Agent authored no commit — this captures the uncommitted workspace tree."
            )
            # Identity via GIT_* env (not -c): env beats every config level, so an
            # ambient/leaked identity can't author devclaw's delivery commit.
            rc, out = await _run(
                "git", "commit", "-m", msg, cwd=workspace_dir,
                env_extra=git_identity_env(),
            )
        if rc != 0:
            result["error"] = f"commit failed: {out}"
            return result
    # else: the agent's own commits are already on this branch (ahead > 0).
    result["committed"] = True

    # Push only if there's a remote. (Local-only repos — e.g. clones of a local
    # path — have no GitHub remote; we stop at the local commit, which is still
    # a reviewable artifact.)
    rc, remote = await _run("git", "remote", "get-url", "origin", cwd=workspace_dir)
    if rc != 0 or not remote.strip():
        result["error"] = "no 'origin' remote — left change on a local branch"
        result["delivered"] = True  # a local branch is still a reviewable result
        return result

    rc, out = await _run("git", "push", "-u", "origin", branch, cwd=workspace_dir)
    if rc != 0:
        result["error"] = f"push failed (check repo push auth): {out[-300:]}"
        result["delivered"] = True  # committed locally; push is what failed
        return result
    result["pushed"] = True

    # Open a PR only on a GitHub remote with gh available/authed. In goal-
    # branch mode, second-and-Nth items push to the SAME branch the first
    # item created a PR on — the existing PR auto-updates; reuse its URL
    # rather than calling `gh pr create` over it (which would fail).
    if "github.com" in remote:
        if goal_mode:
            # A goal-branch PR spans the WHOLE goal, not this one increment. Title
            # rule (same class as _resolve_title): while a SINGLE increment sits on
            # the branch, the worker's own commit subject IS the description of the
            # PR — prefer it over the objective heuristic. Once more increments
            # land, re-title at the goal level (refreshed on every delivery), so
            # the PR never stays frozen at the FIRST increment's subject (e.g.
            # "scaffold … (M1)") while later milestones pile up underneath it —
            # the stale-title bug over an eight-commit branch.
            subjects = await _recent_commit_subjects(workspace_dir, base)
            if subjects:
                # Title from the LATEST increment's commit subject — the same rule
                # _resolve_title applies: when there's a worker commit, it IS the
                # description of the change, never the dispatch prompt. For a single
                # increment subjects[-1] == subjects[0]; for multi-increment the
                # PR is re-titled to the most recent increment on every delivery so
                # it never stays frozen at the first increment's subject.
                s = subjects[-1]
                title = _truncate_words(
                    s if _looks_conventional(s) else _pr_title(s, kind), 72
                )
            else:
                # No worker commits — use MACHINE_COMMIT_SUBJECT (same rule as
                # _resolve_title: the dispatch prompt is not a source for PR titles).
                title = MACHINE_COMMIT_SUBJECT
            body = _goal_pr_body(goal, task_id, verify, subjects, changes=changes, advisories=advisories)
            existing = await _find_pr_for_branch(workspace_dir, branch)
            if existing:
                # Refresh the existing PR to the accumulated state. Best-effort: a
                # refresh hiccup never fails an already-delivered PR.
                await _run(
                    "gh", "pr", "edit", existing,
                    "--title", title, "--body", body,
                    cwd=workspace_dir,
                )
                result["pr_url"] = existing
                result["delivered"] = True
                return result
        else:
            # The title/branch are already issue-decorated by _resolve_title; the
            # body carries what changed + Closes #N. No diff-scope telemetry.
            body = _pr_body(goal, task_id, verify, changes=changes, advisories=advisories)
        base_args = ("--base", base_branch) if base_branch else ()
        rc, out = await _run(
            "gh", "pr", "create", *base_args, "--head", branch,
            "--title", title,
            "--body", body,
            cwd=workspace_dir,
        )
        url = _extract_pr_url(out)
        if url:
            result["pr_url"] = url
            # Light structure: the constant `devclaw` provenance label + an
            # area/kind label so the PR reads like human-managed work
            # (best-effort; a labelling hiccup never fails the delivered PR).
            await _apply_pr_labels(workspace_dir, url, title)
        elif rc != 0:
            result["error"] = f"pushed, but gh pr create failed: {out[-300:]}"

    result["delivered"] = True
    return result
