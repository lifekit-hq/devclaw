"""Evals projection routes (ADR 0006).

Read-only JSON over the ``eval_outcomes`` projection + the ``cycle_reports``
table — what the console's Evals tab reads. Un-prefixed ``.json``
data-endpoint convention (like ``/config/env.json``, ``/projects.json``,
``/control.json``), NOT the contract's illustrative ``/api/evals/...``.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .._state import mcp, store
from ._common import json_limit

@mcp.custom_route("/evals/outcomes/{id}.json", methods=["GET"])
async def evals_outcome_detail_json(request: Request) -> Response:
    """One ``eval_outcomes`` row by integer primary key (the ``id`` column).
    Returns the full row as a plain dict. 404 on unknown id; 400 on non-integer."""
    raw = request.path_params.get("id", "")
    try:
        outcome_id = int(raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad_id"}, status_code=400)
    row = store.get_eval_outcome(outcome_id)
    if row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(row)


@mcp.custom_route("/evals/cycles/{cycle_key}.json", methods=["GET"])
async def evals_cycle_detail_json(request: Request) -> Response:
    """One ``cycle_reports`` row by ``cycle_key`` (the YYYY-MM-DD window-open
    date). Returns the full row. 404 on unknown key."""
    cycle_key = request.path_params.get("cycle_key", "")
    if not cycle_key:
        return JSONResponse({"error": "bad_cycle_key"}, status_code=400)
    row = store.get_cycle_report(cycle_key)
    if row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(row)


@mcp.custom_route("/evals/outcomes.json", methods=["GET"])
async def evals_outcomes_json(request: Request) -> Response:
    """Recent ``eval_outcomes`` projection rows (ADR 0006), newest settle first.
    Params: ``limit`` (default 100, max 1000), ``source`` (``live``|``basket``).
    Read-only; delegates the SELECT to the store (PR1's read method)."""
    limit, err = json_limit(request)
    if err is not None:
        return err
    source = request.query_params.get("source") or None
    if source not in (None, "live", "basket"):
        return JSONResponse({"error": "bad_source"}, status_code=400)
    return JSONResponse(store.list_eval_outcomes(source=source, limit=limit))


@mcp.custom_route("/evals/cycles.json", methods=["GET"])
async def evals_cycles_json(request: Request) -> Response:
    """Recent ``cycle_reports`` rows (ADR 0006), newest window first. Param:
    ``limit`` (default 100, max 1000). The table is bootstrapped by StateStore,
    so an empty table returns [] (never a 500); a real DB fault surfaces loudly."""
    limit, err = json_limit(request)
    if err is not None:
        return err
    return JSONResponse(store.list_cycle_reports(limit=limit))
