"""The project's CI, read as a mechanical fact for the exact PR head (spec 032 US1).

Origin: the 2026-07-06 quarterly benchmark (closeloop-bench-2026-07-05) closed
a goal whose GitHub Actions had failed at startup on all 32 runs — the sandbox
verify gate passed and nothing in the chain ever looked at the repo's actual
check surface. This module became the look. Spec 032 moved it: it used to run
AFTER the done-gate evaluator had spent its LLM call, keyed to the BRANCH head,
and turned "pending" into a worker re-dispatch (the fs-431 churn arc, four
done-gate rounds against a red CI the loop could not read). It now runs BEFORE
the done-check review is dispatched, keys to the delivered PR head, and its
verdict is a fact the tick consumes at zero cognition:

- ``passing``      → the review + evaluator proceed; the head is remembered and
                     merge-on-close requires the SAME head to still be green.
- ``failing``      → no review, no evaluator: the failing checks are steered
                     back as a correction (the worker's next action).
- ``pending``      → the goal holds (``mechanical:ci``) and re-reads on the
                     heartbeat cadence; nothing is judged while CI is running.
- ``unknown``      → gh/network error or a non-GitHub remote: treated like
                     ``pending`` — an uncertain read never approves and never
                     accuses (spec 030 FR-007's posture).
- ``no_pr``        → the branch has no PR (a no-change goal): nothing to read;
                     the close proceeds and merge-on-close reports NO_PR.
- ``no_workflows`` → the repo carries no CI definition on its default branch:
                     a project without a verification environment (Q3 of the
                     spec — not dispatchable; onboarding writes one).
- ``infra_broken`` → every settled check died at startup: the project's own
                     CI definition is broken and no worker may edit it — a
                     typed Problem for the owner.

Which checks count: the base branch's required status checks when branch
protection defines them; otherwise every check on the head. Read from the
repository, never configured in devclaw.

The gh calls live here (not in the tick) so the tick stays a pure,
subprocess-free unit under test; goal_service binds the real checker, tests
inject a fake — the same seam shape as ``merge_on_close``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .. import config as _config
from . import mergeability as _mergeability

#: takes (repo_url, branch), returns the rollup fact for the branch's PR head.
RemoteChecker = Callable[[str, str], Awaitable["RemoteChecksResult"]]

REMOTE_CHECKS_ENABLED = _config.REMOTE_CHECKS_ENABLED

#: conclusions that contradict "this work is done". ``cancelled`` counts: a
#: run that never finished proved nothing. ``startup_failure`` is kept
#: separate: CI infrastructure never executed (billing lock, disabled
#: Actions), which says nothing about the code — see ``infra_broken`` above.
_BAD_CONCLUSIONS = {"failure", "timed_out", "action_required", "cancelled", "error", "stale"}
_INFRA_CONCLUSIONS = {"startup_failure"}
_OK_CONCLUSIONS = {"success", "neutral", "skipped"}
_PENDING_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested", "expected"}

_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_NO_PR_MARKERS = ("no pull requests found", "could not find pull request", "no open pull request")


@dataclass(frozen=True)
class RemoteChecksResult:
    """The rollup fact for one PR head. ``state`` is one of
    passing | failing | pending | infra_broken | no_workflows | no_pr | unknown."""

    state: str
    detail: str = ""
    #: the head the rollup describes — merge-on-close requires the same head
    head_sha: str = ""
    #: the PR the rollup was read from (``""`` for no_pr / unknown)
    pr_url: str = ""
    failing_names: tuple[str, ...] = ()
    pending_names: tuple[str, ...] = ()

    @property
    def proceeds(self) -> bool:
        """May the done-gate review + evaluator run on this fact?"""
        return self.state in ("passing", "no_pr")


def parse_owner_repo(repo_url: str) -> Optional[str]:
    """``https://github.com/o/r.git`` / ``git@github.com:o/r.git`` → ``o/r``.
    None for anything that isn't GitHub — the checker then reports ``unknown``
    rather than guessing at a forge it can't query."""
    if not repo_url:
        return None
    m = _OWNER_REPO_RE.search(repo_url.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _item_name(item: dict) -> str:
    return str(item.get("name") or item.get("context") or "").strip()


def combine_states(
    rollup: Optional[list[dict]],
    *,
    required: Optional[frozenset[str]],
    workflows_present: bool,
    head_sha: str = "",
    pr_url: str = "",
) -> RemoteChecksResult:
    """Fold a PR's ``statusCheckRollup`` into one fact. Pure — the subprocess
    boundary stays in :func:`check_pr`.

    ``rollup`` is the list gh returns (CheckRun items carry ``name``/``status``/
    ``conclusion``; StatusContext items carry ``context``/``state``); ``None``
    means the read itself failed. ``required`` is the base branch's protected
    context set, or ``None`` when the branch has no protection (every check
    counts). A required check that has not reported at all is pending."""
    if rollup is None:
        return RemoteChecksResult("unknown", "could not read the PR's check rollup", head_sha, pr_url)
    if not rollup and not workflows_present:
        return RemoteChecksResult(
            "no_workflows", "no .github/workflows on the default branch — the project has no CI definition",
            head_sha, pr_url,
        )
    items = list(rollup)
    seen: set[str] = set()
    failing: list[str] = []
    pending: list[str] = []
    settled: dict[str, int] = {}
    for it in items:
        name = _item_name(it)
        if required is not None and name not in required:
            continue
        seen.add(name)
        status = str(it.get("status") or "").lower()
        conclusion = str(it.get("conclusion") or it.get("state") or "").lower()
        if status in _PENDING_STATUSES or conclusion in _PENDING_STATUSES or (
            status and status != "completed" and not conclusion
        ):
            pending.append(name)
            continue
        settled[conclusion] = settled.get(conclusion, 0) + 1
        if conclusion in _BAD_CONCLUSIONS:
            failing.append(name)
    if required is not None:
        pending.extend(sorted(required - seen))
    summary = ", ".join(f"{n}× {c or '(none)'}" for c, n in sorted(settled.items())) or "no settled checks"
    if failing:
        return RemoteChecksResult(
            "failing", f"{len(failing)} failing: {', '.join(failing)} ({summary})",
            head_sha, pr_url, tuple(failing), tuple(pending),
        )
    if pending:
        return RemoteChecksResult(
            "pending", f"{len(pending)} still running: {', '.join(pending)} ({summary})",
            head_sha, pr_url, (), tuple(pending),
        )
    if not settled:
        # workflows exist but nothing has reported for this head yet
        return RemoteChecksResult(
            "pending", "workflows exist but no check has reported for this head yet",
            head_sha, pr_url,
        )
    infra = sum(n for c, n in settled.items() if c in _INFRA_CONCLUSIONS)
    ok = sum(n for c, n in settled.items() if c in _OK_CONCLUSIONS)
    if infra and not ok:
        return RemoteChecksResult(
            "infra_broken",
            f"{infra} of {len(items)} died at startup — CI infrastructure never "
            f"executed (Actions permissions/billing on the repo) ({summary})",
            head_sha, pr_url,
        )
    return RemoteChecksResult("passing", f"{ok} checks green ({summary})", head_sha, pr_url)


async def _gh(*args: str) -> tuple[int, str]:
    """``gh`` under the shared wall-clock bound; a spawn failure or a timeout
    reads as ``rc != 0`` so the caller maps it to ``unknown``."""
    rc, out = await _mergeability.run_bounded("gh", *args)
    return (rc if rc != 0 else 0), out


def _parse_json(rc: int, out: str) -> Optional[object]:
    if rc != 0:
        return None
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        return None


async def check_pr(repo_url: str, branch: str) -> RemoteChecksResult:
    """The real reader: the PR for ``branch`` on ``repo_url``, its head, its
    check rollup, and the base branch's required contexts. Best-effort
    throughout — every failure path degrades to ``unknown`` rather than
    raising into the tick; only a gh reply that says "no PR" is ``no_pr``."""
    owner_repo = parse_owner_repo(repo_url)
    if not owner_repo:
        return RemoteChecksResult("unknown", f"not a GitHub remote: {repo_url!r}")

    rc, out = await _gh(
        "pr", "view", branch, "--repo", owner_repo,
        "--json", "url,headRefOid,baseRefName,statusCheckRollup",
    )
    if rc != 0:
        low = out.lower()
        if any(m in low for m in _NO_PR_MARKERS):
            return RemoteChecksResult("no_pr", f"no PR for {branch}")
        return RemoteChecksResult("unknown", f"could not read the PR for {branch!r}: {out.strip()[:200]}")
    pr = _parse_json(rc, out)
    if not isinstance(pr, dict):
        return RemoteChecksResult("unknown", f"unparseable PR payload for {branch!r}")
    head_sha = str(pr.get("headRefOid") or "")
    pr_url = str(pr.get("url") or "")
    base = str(pr.get("baseRefName") or "")
    rollup_raw = pr.get("statusCheckRollup")
    rollup = [it for it in rollup_raw if isinstance(it, dict)] if isinstance(rollup_raw, list) else None

    required: Optional[frozenset[str]] = None
    if base:
        rc_p, out_p = await _gh(
            "api", f"repos/{owner_repo}/branches/{base}/protection/required_status_checks/contexts",
        )
        contexts = _parse_json(rc_p, out_p)
        if isinstance(contexts, list):
            required = frozenset(str(c) for c in contexts if c) or None
        # rc != 0 (HTTP 404 = no protection, or any read failure) → every check counts

    workflows_present = True
    if not rollup:
        rc_wf, out_wf = await _gh(
            "api", f"repos/{owner_repo}/contents/.github/workflows?ref={base or 'HEAD'}",
            "--jq", "length",
        )
        workflows_present = rc_wf == 0 and out_wf.strip().isdigit() and int(out_wf.strip()) > 0

    return combine_states(
        rollup, required=required, workflows_present=workflows_present,
        head_sha=head_sha, pr_url=pr_url,
    )


def default_checker() -> RemoteChecker:
    """The production checker (real gh). Indirected so goal_service can bind it
    and tests inject a recording fake — the merge_on_close seam shape."""
    return check_pr


# ---- PR-state read for the pr_ledger refresh (spec 018 US2) ----------------

#: injectable seam, same pattern as :data:`RemoteChecker`: the cycle-report
#: refresh binds :func:`pr_state`; tests inject a fake so the stubbed suite
#: never spawns gh.
PrStateFetcher = Callable[[str], Awaitable[str]]


async def pr_state(pr_url: str) -> str:
    """Ground-truth state of one delivered PR: ``merged`` | ``rejected`` |
    ``open`` | ``unknown``. Any failure (gone repo, auth, network, weird
    payload) is ``unknown`` — reported as such, never guessed (spec 018
    FR-004: this is telemetry, so it fails loud in the numbers, not closed)."""
    rc, out = await _gh("pr", "view", pr_url, "--json", "state")
    if rc != 0:
        return "unknown"
    try:
        st = json.loads(out).get("state")
    except (ValueError, AttributeError):
        return "unknown"
    return {"MERGED": "merged", "CLOSED": "rejected", "OPEN": "open"}.get(st, "unknown")
