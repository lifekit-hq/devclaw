"""GitHub webhook ingress — one HMAC-verified route whose only effect is to
wake the goal tick early (a comment, a push, a check run: the world moved).
Unset ``DEVCLAW_WEBHOOK_SECRET`` ⇒ 404, no unauthenticated surface."""

from __future__ import annotations

import hashlib
import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import config as _config
from .._state import goals, mcp


def _verify(secret: str, body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


@mcp.custom_route("/webhooks/github", methods=["POST"])
async def github_webhook(request: Request) -> Response:
    secret = _config.webhook_secret()
    if not secret:
        return Response(status_code=404)
    body = await request.body()
    if not _verify(secret, body, request.headers.get("X-Hub-Signature-256", "")):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    if request.headers.get("X-GitHub-Event", "") == "ping":
        return JSONResponse({"outcome": "pong"}, status_code=200)
    goals.poke()
    return JSONResponse({"accepted": True}, status_code=202)
