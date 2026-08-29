"""GitHub webhook ingress (spec 023) — one authenticated route, nothing else.

``POST /webhooks/github``: HMAC-verified deliveries hand off to the goal
layer's event router (``devclaw.goal.events``), which wakes the existing
machinery — no state is written here. The route answers FAST (202): grading
work is fired as a background task so GitHub's delivery timeout never waits
on cognition.

Security posture: the secret lives in ``DEVCLAW_WEBHOOK_SECRET``; while it is
unset the route answers 404 — the feature is OFF and no unauthenticated
surface exists (fail-safe). A bad signature is a counted 401. The route is
the only path meant to be exposed publicly (Tailscale Funnel scoped to
``/webhooks/github``); everything else stays tailnet-internal — see
docs/runbooks/webhooks.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import config as _config
from ...goal import events as _events
from ...intake import regrade as _regrade
from .._state import goals, mcp, registry

#: grading tasks in flight — held so the loop never garbage-collects one
#: mid-run; bounded by GitHub's own delivery pacing.
_background: "set[asyncio.Task]" = set()


def _verify(secret: str, body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


@mcp.custom_route("/webhooks/github", methods=["POST"])
async def github_webhook(request: Request) -> Response:
    secret = _config.webhook_secret()
    if not secret:
        return Response(status_code=404)  # feature off — no surface at all
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify(secret, body, sig):
        sys.stderr.write("webhook: rejected delivery with a bad signature\n")
        return JSONResponse({"error": "bad signature"}, status_code=401)
    event = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "unparseable payload"}, status_code=400)
    if event == "ping":
        return JSONResponse({"outcome": "pong"}, status_code=200)

    async def _route() -> None:
        try:
            outcome = await _events.route_event(
                event, payload,
                registry=registry,
                goal_store=goals._goal_store,
                poke=goals.poke,
                regrade=_regrade,
            )
            sys.stderr.write(
                f"webhook: {event}/{payload.get('action', '')} → "
                f"{outcome['outcome']} ({outcome['detail']})\n"
            )
        except Exception as exc:  # noqa: BLE001 — never let a payload kill the loop
            sys.stderr.write(f"webhook: router crashed: {exc}\n")

    # Answer 202 immediately; the router (and any grading cognition) runs in
    # the background so the delivery round-trip stays milliseconds.
    task = asyncio.create_task(_route())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return JSONResponse({"accepted": True}, status_code=202)
