"""Self-deploy on merge (spec 025 US2) — the instance redeploys itself onto
its own merged main, quiescence-gated.

Mechanical end to end (zero cognition): the merge-on-close path records a
``deploy_pending`` meta row for a devclaw-repo goal; the heartbeat calls
:func:`maybe_trigger` after every sweep, which is a free meta read when
nothing is owed, waits for task quiescence (``count_running() == 0`` —
running only, NOT ``has_active_work``, which counts pending and would
deadlock against a full queue), and then fires the spec-005 deploy workflow
(``gh workflow run deploy.yml``) whose auto lane owns the probe + one
rollback on the runner side. Recreating ``devclaw-mcp`` SIGKILLs in-flight
sandboxes (spec 005's edge case) — the quiescence gate exists so
``queue.recover()`` stays a safety net, not a routine.

This reverses spec 005 FR-008's "operator-triggered ONLY" doctrine — ruled
by Denys 2026-08-29 (spec 025); the workflow keeps its manual lane
byte-compatible.
"""

from __future__ import annotations

import sys

from .. import config as _config
from .merge_on_close import _run_gh


def is_self_repo(repo_url: "str | None") -> bool:
    """Whether ``repo_url`` names devclaw's own repo (``DEVCLAW_SELF_REPO``,
    ``owner/name``). Unset slug ⇒ never — the whole feature is a no-op, same
    gating as self-issue filing."""
    slug = _config.self_repo()
    if not slug or not repo_url:
        return False
    normalized = repo_url.strip().rstrip("/").removesuffix(".git").lower()
    return normalized.endswith("/" + slug.lower()) or normalized == slug.lower()


async def trigger_workflow(slug: str) -> "tuple[bool, str]":
    """Fire the deploy workflow's auto lane. Blank ``tag`` ⇒ the workflow
    builds the current main HEAD and deploys its SHA — exactly what a
    just-merged goal wants."""
    rc, out = await _run_gh(
        "gh", "workflow", "run", "deploy.yml", "-R", slug,
        "-f", "auto=true",
    )
    return rc == 0, out


#: patchable seam for tests (the snapshot-collector convention).
_trigger = trigger_workflow


async def maybe_trigger(state, *, now_ms: int, log=None) -> "str | None":
    """The heartbeat edge. Returns what happened (``"triggered"`` /
    ``"expired"`` / ``"trigger_failed"``) or ``None`` when nothing fired.
    Never raises — the caller wraps it anyway (a deploy edge must not kill
    the heartbeat), but a crash here should degrade to a loud stderr line,
    not an exception."""
    def _log(line: str) -> None:
        if log is not None:
            try:
                log(line)
            except Exception:  # noqa: BLE001 — logging is best-effort
                pass
        sys.stderr.write(f"goal-layer: self-deploy: {line}\n")

    pending = state.deploy_pending()
    if pending is None:
        return None  # the zero-cost idle read
    sha, goal_id, since_ms = pending
    if now_ms - since_ms > _config.deploy_quiescence_s() * 1000:
        state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="expired",
                                 at_ms=now_ms,
                                 detail="quiescence never arrived within the bounded wait")
        state.clear_deploy_pending()
        _log(f"pending deploy of {sha[:12] or 'main'} EXPIRED — quiescence never "
             f"arrived; re-armed by the next devclaw-repo close or operator resume")
        return "expired"
    if state.count_running() > 0:
        return None  # not quiescent yet — check again next heartbeat
    slug = _config.self_repo()
    if not slug:
        state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="trigger_failed",
                                 at_ms=now_ms, detail="DEVCLAW_SELF_REPO unset")
        state.clear_deploy_pending()
        _log("cannot trigger: DEVCLAW_SELF_REPO unset")
        return "trigger_failed"
    ok, out = await _trigger(slug)
    if ok:
        state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="triggered",
                                 at_ms=now_ms)
        state.clear_deploy_pending()
        _log(f"triggered deploy workflow for {sha[:12] or 'main HEAD'} (goal {goal_id})")
        return "triggered"
    state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="trigger_failed",
                             at_ms=now_ms, detail=out[:300])
    state.clear_deploy_pending()
    _log(f"workflow trigger FAILED: {out[:200]}")
    return "trigger_failed"
