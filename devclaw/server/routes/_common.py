"""Helpers shared by more than one route module.

Anything here is used by at least two modules. A helper used by exactly one
route module belongs in that module — this is not a junk drawer.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Default/ceiling for the `limit` query param on list endpoints.
JSON_DEFAULT_LIMIT = 100
JSON_MAX_LIMIT = 1000


def json_limit(request: Request) -> "tuple[int, Response | None]":
    """Parse and clamp the ``limit`` query param for a list endpoint.

    Returns ``(limit, None)`` or ``(0, 400-response)``. Was ``_evals_limit`` in
    ``http.py`` — the name predated ``/problems.json`` adopting it, and the
    misnomer is why it looked evals-specific when the evals routes were split
    out (it was not, and the split broke problems until this moved here).
    """
    try:
        limit = int(request.query_params.get("limit", JSON_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return 0, JSONResponse({"error": "bad_limit"}, status_code=400)
    if limit <= 0:
        return 0, JSONResponse({"error": "bad_limit"}, status_code=400)
    return min(limit, JSON_MAX_LIMIT), None
