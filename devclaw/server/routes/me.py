"""The signed-in console user's own preferences: ``GET|PATCH /api/me/preferences``,
the account tier of the lifekit app-shell standard (``accountPreferencesStore``
in ``@lifekit-hq/core``). Today that is the appearance (theme mode, seed colour,
intensity) and nothing else; it is presentation, read by no gate.

The identity is the oauth2-proxy email the edge forwards as
``X-Auth-Request-Email`` (Traefik's ``sso`` forwardAuth replaces any client copy
on the console route). A request without it - the old ``?token=`` links, a
machine client - gets 401 and the console keeps the appearance on the device.
The bearer gate still applies first; the header only names whose row it is, so
a holder of the bearer is as trusted here as everywhere else."""

from __future__ import annotations

import re

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .._state import mcp, store

IDENTITY_HEADER = "X-Auth-Request-Email"
_THEMES = ("system", "light", "dark")
_SEED = re.compile(r"#[0-9a-fA-F]{6}")


def _read(subject: str) -> dict:
    stored = store.get_appearance(subject)
    if stored is None:
        return {"appearance": {"theme": "system"}}
    appearance: dict = {"theme": stored["theme"]}
    if stored["seed"] is not None:
        appearance["seed"] = stored["seed"]
    if stored["intensity"] is not None:
        appearance["intensity"] = stored["intensity"]
    return {"appearance": appearance}


def _validate(appearance: object) -> "dict[str, str]":
    if not isinstance(appearance, dict):
        return {"appearance": "must be an object"}
    errors: dict[str, str] = {}
    if appearance.get("theme") not in _THEMES:
        errors["appearance.theme"] = "must be system, light or dark"
    seed = appearance.get("seed")
    if seed is not None and not (isinstance(seed, str) and _SEED.fullmatch(seed)):
        errors["appearance.seed"] = "must be a #rrggbb colour"
    intensity = appearance.get("intensity")
    if intensity is not None and not (
        isinstance(intensity, (int, float)) and not isinstance(intensity, bool)
        and 0 <= intensity <= 1
    ):
        errors["appearance.intensity"] = "must be between 0 and 1"
    return errors


def _subject(request: Request) -> str:
    return request.headers.get(IDENTITY_HEADER, "").strip().lower()


_UNSIGNED = {"status": "unauthorized", "problem": "sign in through the edge"}


@mcp.custom_route("/api/me/preferences", methods=["GET"])
async def me_preferences(request: Request) -> Response:
    subject = _subject(request)
    if not subject:
        return JSONResponse(_UNSIGNED, status_code=401)
    return JSONResponse(_read(subject))


@mcp.custom_route("/api/me/preferences", methods=["PATCH"])
async def me_preferences_patch(request: Request) -> Response:
    """Any subset of the top-level fields: a present ``appearance`` replaces the
    stored one wholesale (an omitted seed clears it), an absent one is untouched.
    The full object comes back."""
    subject = _subject(request)
    if not subject:
        return JSONResponse(_UNSIGNED, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"status": "invalid", "problem": "body must be a JSON object"},
                            status_code=400)
    if "appearance" in body:
        appearance = body["appearance"]
        errors = _validate(appearance)
        if errors:
            return JSONResponse({"status": "invalid", "problem": "invalid preferences",
                                 "errors": errors}, status_code=400)
        seed = appearance.get("seed")
        store.set_appearance(subject, appearance["theme"],
                             seed.lower() if isinstance(seed, str) else None,
                             float(appearance["intensity"])
                             if appearance.get("intensity") is not None else None)
    return JSONResponse(_read(subject))
