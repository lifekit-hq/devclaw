"""Self-deploy on merge: the instance redeploys itself onto its own merged
main once no session is running. Armed by the deploy workflow's push-to-main
job (``POST /control/deploy-pending``); fired here, mechanically."""

from __future__ import annotations

import sys

from .. import config as _config
from .github import gh


async def trigger_workflow(slug: str) -> "tuple[bool, str]":
    rc, out = await gh("workflow", "run", "deploy.yml", "-R", slug, "-f", "auto=true")
    return rc == 0, out


_trigger = trigger_workflow


async def maybe_trigger(state, *, now_ms: int) -> "str | None":
    """``triggered`` / ``expired`` / ``trigger_failed`` / None. Never raises."""
    pending = state.deploy_pending()
    if pending is None:
        return None
    sha, goal_id, since_ms = pending
    if now_ms - since_ms > _config.deploy_quiescence_s() * 1000:
        state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="expired", at_ms=now_ms,
                                 detail="quiescence never arrived within the bounded wait")
        state.clear_deploy_pending()
        sys.stderr.write(f"goal-layer: self-deploy of {sha[:12] or 'main'} EXPIRED\n")
        return "expired"
    if state.count_running() > 0:
        return None
    slug = _config.self_repo()
    if not slug:
        state.record_deploy_last(sha=sha, goal_id=goal_id, outcome="trigger_failed",
                                 at_ms=now_ms, detail="DEVCLAW_SELF_REPO unset")
        state.clear_deploy_pending()
        return "trigger_failed"
    ok, out = await _trigger(slug)
    outcome = "triggered" if ok else "trigger_failed"
    state.record_deploy_last(sha=sha, goal_id=goal_id, outcome=outcome, at_ms=now_ms, detail=out[:300])
    state.clear_deploy_pending()
    sys.stderr.write(f"goal-layer: self-deploy {outcome} for {sha[:12] or 'main HEAD'}\n")
    return outcome
