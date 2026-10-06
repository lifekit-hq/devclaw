"""Self-deploy on merge: the instance redeploys itself onto its own merged
main once no session is running. Armed by the deploy workflow's push-to-main
job (``POST /control/deploy-pending``) or, when that never lands (a hand
merge), by ``reconcile`` noticing the running build trails main; fired here."""

from __future__ import annotations

import sys

from .. import config as _config
from .github import gh


async def trigger_workflow(slug: str) -> "tuple[bool, str]":
    rc, out = await gh("workflow", "run", "deploy.yml", "-R", slug, "-f", "auto=true")
    return rc == 0, out


_trigger = trigger_workflow

_SETTLE_MS = 3_600_000  # a deploy fired this recently is still landing — not a trail


async def main_head(slug: str) -> str:
    rc, out = await gh("api", f"repos/{slug}/commits/main", "--jq", ".sha")
    return out.strip() if rc == 0 else ""


_main_head = main_head


async def reconcile(state, *, now_ms: int) -> bool:
    """Arm a deploy when the running build trails main and no arm is pending.

    The push-to-main arm job is the fast path; a merge done by hand, or an arm
    that could not reach the instance, leaves main ahead with nothing armed.
    A SHA already attempted (deployed, rolled back, failed) is never re-armed,
    so a rollback cannot loop. Never raises."""
    running, slug = _config.git_sha(), _config.self_repo()
    if not running or not slug or state.deploy_pending() is not None:
        return False
    last = state.deploy_last() or {}
    if last and now_ms - int(last.get("at_ms") or 0) < _SETTLE_MS:
        return False
    head = await _main_head(slug)
    if not head or head == running or head == last.get("sha"):
        return False
    state.set_deploy_pending(sha=head, goal_id="reconcile", since_ms=now_ms)
    sys.stderr.write(f"goal-layer: running {running[:12]} trails main {head[:12]}; self-deploy armed\n")
    return True


async def maybe_trigger(state, *, now_ms: int) -> "str | None":
    """``triggered`` / ``expired`` / ``trigger_failed`` / None. Never raises."""
    pending = state.deploy_pending()
    if pending is None:
        if not await reconcile(state, now_ms=now_ms):
            return None
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
