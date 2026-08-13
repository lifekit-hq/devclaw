"""Deliver a completed task's change as a reviewable branch + PR.

After a task settles ``done`` (the verify gate passed), the agent's change is
sitting **uncommitted** in the workspace. Delivery turns that into something you
*review* instead of *produce*: a branch, a commit, a push, and — if the remote
is GitHub and ``gh`` is authed — a pull request whose URL is recorded on the task.

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

import asyncio
import os
import re

from ..git_identity import git_identity_env


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
    return s[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _pr_title(goal: str, kind: str | None = None, limit: int = 72) -> str:
    """A clean, conventional-commit-style title (e.g. `feat: add a /health
    endpoint`) — not the raw goal truncated mid-word."""
    summary = _clean_summary(goal)
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
# Conservative on purpose: only explicit fix/close verbs and the self-fix
# ``issue #N`` objective count, so a passing mention (``see #99 for context``)
# never triggers a spurious auto-close.
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
        for pattern in (_CLOSES_RE, _ISSUE_RE):
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
    long_lived tick, so it must NEVER leak into a PR title."""
    return goal.strip().startswith("Advance this goal by one substantive")


def _objective_from_brief(goal: str) -> str:
    """Pull the ``Goal: <objective>`` line out of the advance-brief — a usable
    title basis when the worker committed nothing to derive one from."""
    for line in goal.splitlines():
        s = line.strip()
        if s.startswith("Goal:"):
            return s[len("Goal:") :].strip()
    return ""


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
    # No worker commit to describe the change (dirty tree, nothing committed).
    # Derive from the goal — but the thin-advance brief is plumbing, not a
    # description, so fall back to its embedded objective rather than leak it.
    base_text = goal
    if _is_advance_brief(goal):
        base_text = _objective_from_brief(goal) or "advance the goal by one increment"
    title = _pr_title(base_text, kind)
    branch = f"devclaw/{task_id[:8]}-{_slug(base_text)}"
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


def _pr_body(
    goal: str, task_id: str, verify: dict | None,
    *, changes: str | None = None, advisories: list | None = None,
) -> str:
    """A PR body that reads like a careful engineer opened it: lead with what
    CHANGED (the engineer's own commit body when it wrote one, else the task),
    a one-line verification note, ``Closes #N`` for any resolved issue, the
    original task tucked into a collapsed block, and a plain devclaw signature.
    No diffstat, no telemetry.

    ``advisories`` (ADR 0007): trust-mode dial-able gate findings this change
    SHIPPED past rather than blocked on. Rendered as a loud section so the human
    sees them at the merge boundary — the backstop for advisory gates."""
    lead = _strip_directive_lines(changes) if changes is not None else goal.strip()
    parts = [lead or "(see commit)", ""]
    if verify and verify.get("ran"):
        cmd = verify.get("cmd", "")
        if verify.get("passed"):
            parts += [f"Verified with `{cmd}` — passing.", ""]
        else:
            parts += [f"Gate `{cmd}` did **not** pass — see the task error.", ""]
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
    if changes is not None:
        parts += ["<details><summary>Original task</summary>", "",
                  goal.strip(), "", "</details>", ""]
    parts += ["---", f"🤖 Delivered by devclaw (task `{task_id}`)"]
    return "\n".join(parts)


async def _run(
    prog: str, *args: str, cwd: str, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run a command, return (exit_code, combined-output). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            prog, *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **env_extra} if env_extra else None,
        )
    except OSError as exc:
        return 127, f"{prog} not runnable: {exc}"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


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
) -> dict:
    """Commit the workspace's change to a branch and (best-effort) push + open a PR.
    Returns a verdict dict; never raises. ``kind`` shapes the conventional-commit
    title (feat/fix/…); ``verify`` (the gate verdict) goes into the PR body.
    ``title`` is the PLANNER's chosen PR title (see Action.title / plan.md
    §Production-ready C7). When present and non-empty it wins over the
    engineer's own commit subject and the goal-derived heuristic; a resolved
    issue reference then decorates it (title ``(#N)`` + branch) via
    ``_resolve_title``.

    ``base_branch`` / ``target_branch`` are the v1-helper-resurface delivery
    seam (docs/proposals/v1-helper-resurface.md §3): ``base_branch`` is a
    caller-chosen PR base — it becomes the ahead-count/diff base ref and the
    ``gh pr create --base``; ``target_branch`` pins the delivery branch — when
    the workspace is already ON it, delivery stays there and reuses its single
    PR (the same machinery goal branches use, no new reuse logic). Both default
    to None ⇒ byte-identical legacy behavior (fresh derived branch → the
    remote's default base)."""
    result: dict = {"delivered": False, "branch": None, "committed": False,
                    "pushed": False, "pr_url": None, "error": None}

    rc, _ = await _run("git", "rev-parse", "--is-inside-work-tree", cwd=workspace_dir)
    if rc != 0:
        result["error"] = "workspace is not a git repository"
        return result

    rc, status = await _run("git", "status", "--porcelain", cwd=workspace_dir)
    dirty = rc == 0 and bool(status.strip())

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

    if not dirty and ahead == 0:
        result["error"] = "no changes to deliver"
        return result

    # Detect branch-reuse mode: prepare_workspace(branch=...) put the workspace
    # on its delivery branch BEFORE the agent ran — either a goal branch
    # (``goal/<id>``) or a caller-pinned ``target_branch`` (the v1-helper
    # delivery seam). In that case all commits the agent made are already on
    # that branch — we push it as-is (no new branch), and its single PR is
    # reused across deliveries. Legacy mode (workspace on the default branch,
    # off-goal, and unpinned) creates a per-task branch the way it always has.
    current = await _current_branch(workspace_dir)
    goal_mode = bool(current) and (
        current.startswith("goal/") or (target_branch is not None and current == target_branch)
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
    agent_msg = await _agent_commit_msg(workspace_dir, base) if ahead > 0 else None
    title_slot, derived_branch, changes = _resolve_title(
        planner_title=title, agent_msg=agent_msg, goal=goal, kind=kind, task_id=task_id,
    )
    # ``title`` (the function parameter) has now been consumed; ``title_slot`` is
    # the PR-title string the rest of this function uses. Kept as ``title`` in the
    # commit-message path below so we don't churn the message shape.
    title = title_slot

    if goal_mode:
        # Stay on the goal branch — every item commits to it cumulatively.
        branch = current  # type: ignore[assignment]
        result["branch"] = branch
    else:
        branch = derived_branch
        result["branch"] = branch
        # Put the change on its branch. `checkout -b` carries HEAD — including
        # any commits the agent already made — onto the new branch. A feature
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

    if dirty:
        await _run("git", "add", "-A", cwd=workspace_dir)
        msg = f"{title}\n\nDelivered by devclaw (task {task_id})."
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
            existing = await _find_pr_for_branch(workspace_dir, branch)
            if existing:
                result["pr_url"] = existing
                result["delivered"] = True
                return result
        # The title/branch are already issue-decorated by _resolve_title; the
        # body carries what changed + Closes #N. No diff-scope telemetry.
        base_args = ("--base", base_branch) if base_branch else ()
        rc, out = await _run(
            "gh", "pr", "create", *base_args, "--head", branch,
            "--title", title,
            "--body", _pr_body(goal, task_id, verify, changes=changes, advisories=advisories),
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
