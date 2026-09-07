"""A worker-reported environment gap becomes devclaw work, at the hold (spec 038).

The worker types ``BLOCKED: env — <item>``, the project holds on
``mechanical:env``, and the operator is told the gap is filed as devclaw work.
Until #818 nothing filed it: filing lived on the once-per-cycle self-issue edge
behind a two-run-cycle recurrence bar, and the hold this very settle places is
what stops the failure from ever recurring. The gate and the brake deadlocked,
silently.

So the filing happens HERE, in the same settle as the hold, through the issue
doorway (:mod:`devclaw.issue_doorway`) — the ONE machine-issue writer. The
recurrence bar is right for noisy transient failures and is left alone; this
class is deterministic, terminal by construction, and known the instant it is
reported, so it does not belong behind a noise filter.

Everything here is mechanical: no LLM, and no subprocess at all unless
``DEVCLAW_SELF_REPO`` names a repo. The verb never raises and never touches the
block — the hold is what protects the project's sessions, and bookkeeping must
not be able to break it. Its whole output is one operator-readable clause the
caller threads into the goal log, the ``blocked_on`` text and the owner ping —
plus a problems-catalog row whenever an attempt failed, so a filing devclaw
could not perform is countable and not merely readable on one goal's hold.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Optional

from .. import issue_doorway as _doorway
from ..state_store.problems import (
    ENV_DEFICIENCY_CATEGORY, ENV_DEFICIENCY_KIND, fingerprint_for,
)
from . import mergeability as _mergeability
from . import self_issue as _self_issue

#: The doorway ``source`` this producer files under. Rides the issue title and
#: the metadata line, so a machine consumer can tell an environment gap from a
#: catalog recurrence (``self_issue_catalog``) without parsing prose.
DOORWAY_SOURCE = "env_deficiency"

#: How long the filing may hold the heartbeat. The tick already bounds every
#: ``gh`` read it makes (``goal/remote_checks._gh``); a filing that hangs must
#: not hang the whole sweep, and the doorway's own adapter is unbounded because
#: its other producer runs on the once-per-cycle edge.
FILING_TIMEOUT_S = _mergeability.GH_TIMEOUT_S


@dataclass(frozen=True)
class EnvFilingOutcome:
    """What the filing attempt did, as one clause for a human plus the issue
    number for a caller that wants to link it."""

    #: e.g. ``filed as devclaw work: #42 (https://…/issues/42)`` or
    #: ``NOT filed as devclaw work: DEVCLAW_SELF_REPO is unset on this instance``.
    line: str
    issue_number: Optional[int] = None
    filed: bool = False


def fingerprint_for_item(item: str) -> str:
    """The catalog fingerprint of the deficiency row for ``item`` — the ONE
    identity the catalog, the doorway ledger and the cycle-close filer share."""
    return fingerprint_for(ENV_DEFICIENCY_CATEGORY, ENV_DEFICIENCY_KIND, item)


def issue_url(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def _finding(
    item: str, *, goal_id: str, project_id: Optional[str], cap_id: str, task_id: str,
) -> "_doorway.MachineFinding":
    where = ", ".join(
        part for part in (
            f"project {project_id}" if project_id else "",
            f"goal {goal_id}" if goal_id else "",
            f"task {task_id}" if task_id else "",
        ) if part
    ) or "unknown"
    return _doorway.MachineFinding(
        source=DOORWAY_SOURCE,
        fingerprint=fingerprint_for_item(item),
        title=f"sandbox lacks {item}",
        evidence=(
            f"A worker stopped and reported `BLOCKED: env — {item}`: the sandbox "
            "cannot do something the work needs (a tool, a service, a credential "
            "or network access).\n\n"
            f"Reported by: {where}\n"
            f"Capability row: {cap_id}\n\n"
            "Every goal on the project is held on `mechanical:env` until the "
            "instance environment changes. The hold heals mechanically once the "
            "capability is provided — this issue tracks providing it."
        ),
        expected=f"the sandbox provides {item} to work on this project",
        actual=f"the sandbox does not provide {item}; the project is held",
        severity="high",
        proposed_done_when=(
            f"The sandbox provides {item} — through the image, a mise tool, or the "
            "project's environment declaration — so a worker on this project no "
            "longer reports it missing and the `mechanical:env` hold clears."
        ),
    )


def _not_filed(store, repo: str, item: str, reason: str) -> EnvFilingOutcome:
    """A filing that failed where the doorway could not record it itself
    (:func:`_doorway.record_filing_failure` says which cases those are) — the
    row, then the clause a human reads."""
    try:
        _doorway.record_filing_failure(
            store, repo=repo, source=DOORWAY_SOURCE,
            fingerprint=fingerprint_for_item(item), reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping never breaks the hold
        sys.stderr.write(f"env-issue: recording the failed filing of {item!r}: {exc}\n")
    return EnvFilingOutcome(line=f"NOT filed as devclaw work: {reason}")


async def file_env_deficiency(
    store,
    *,
    goal_id: str,
    project_id: Optional[str],
    item: str,
    cap_id: str,
    task_id: str = "",
    repo: Optional[str] = None,
    gh: "_doorway.GhAdapter | None" = None,
    now_ms: Optional[int] = None,
) -> EnvFilingOutcome:
    """File ``item`` as devclaw work and say what happened.

    ``store`` is the :class:`~devclaw.goal.store.base.GoalStore` — the ledger
    and catalog writes ride its thin passthroughs, so the goal layer never
    reaches into the shared state store itself.

    Unset ``DEVCLAW_SELF_REPO`` is a stated gate, not a failure: nothing is
    spawned, nothing is recorded, and the reason is named. Every attempt that
    DID fail is loud on both surfaces — the returned clause and exactly one
    catalog row.
    """
    repo = repo or _self_issue.self_repo()
    if not repo:
        return EnvFilingOutcome(
            line=(
                "NOT filed as devclaw work: DEVCLAW_SELF_REPO is unset on this "
                "instance, so devclaw has no repo to file against"
            )
        )
    try:
        # What escapes ``file_finding`` is finding validation and the
        # wall-clock bound. Both degrade to a stated, recorded non-filing —
        # never into the settle.
        outcome = await asyncio.wait_for(
            _doorway.file_finding(
                _finding(item, goal_id=goal_id, project_id=project_id,
                         cap_id=cap_id, task_id=task_id),
                repo=repo, store=store, gh=gh,
                labels=[_self_issue.SELF_FILED_LABEL, f"class:{ENV_DEFICIENCY_CATEGORY}"],
                now_ms=now_ms,
            ),
            timeout=FILING_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # The create may have landed on GitHub before the bound cut the call,
        # with no ledger row to prove it — say so, because the next occurrence
        # of this gap would then open a second issue for one root cause.
        return _not_filed(
            store, repo, item,
            f"gh did not answer within {FILING_TIMEOUT_S:g}s "
            "(an issue may exist with no ledger row behind it)",
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping never breaks the hold
        return _not_filed(store, repo, item, f"{type(exc).__name__}: {exc}")

    if not outcome.ok:
        # The doorway saw this one and recorded it; a second row here would
        # double-count one failure.
        return EnvFilingOutcome(
            line=f"NOT filed as devclaw work: {outcome.reason or 'filing failed'}"
        )
    if outcome.issue_number is None:
        return _not_filed(
            store, repo, item, f"the doorway reported {outcome.action} with no issue number"
        )
    number = outcome.issue_number
    # Link the catalog row so the once-per-cycle filer reads it as already
    # tracked (``should_file`` skips an open issue) and the console's problem
    # lifecycle renders *filed* instead of *identified*.
    try:
        store.set_problem_issue(
            fingerprint_for_item(item), issue_number=number, issue_state="open"
        )
    except Exception as exc:  # noqa: BLE001 — the issue exists; the link is a view
        sys.stderr.write(f"env-issue: linking problem row to #{number} failed: {exc}\n")
    verb = "filed" if outcome.action == "filed" else "already tracked"
    return EnvFilingOutcome(
        line=f"{verb} as devclaw work: #{number} ({issue_url(repo, number)})",
        issue_number=number,
        filed=outcome.action == "filed",
    )
