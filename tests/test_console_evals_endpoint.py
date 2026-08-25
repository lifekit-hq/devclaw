"""Regression tests for the console Evals surfaces (ADR 0006 PR3):

  * ``GET /evals/outcomes.json`` — read-only projection over ``eval_outcomes``
    (params: limit, source). Pins that it returns the store's rows newest-first,
    honours the source filter, and 400s (never silently mis-filters) on bad
    input.
  * ``GET /evals/cycles.json`` — read-only ``cycle_reports`` list. Pins that an
    empty table returns ``[]`` (never a 500) and that recorded cycles come back;
    the store read re-raises a real ``OperationalError`` (locked/corrupt DB)
    rather than masking it as an empty clean-cycle list.
  * ``GET /evals/outcomes/{id}.json`` — single eval_outcomes row by integer id;
    404 on unknown.
  * ``GET /evals/cycles/{cycle_key}.json`` — single cycle_reports row by key;
    404 on unknown.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from starlette.requests import Request

from devclaw.state_store import StateStore


def _store(tmp_path):
    return StateStore(str(tmp_path / "s.db"))


def _get(fn, query: str = ""):
    scope = {
        "type": "http",
        "method": "GET",
        "path_params": {},
        "headers": [],
        "query_string": query.encode(),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, json.loads(resp.body)


def _get_path(fn, path_params: dict):
    scope = {
        "type": "http",
        "method": "GET",
        "path_params": path_params,
        "headers": [],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, json.loads(resp.body)


# ── /evals/outcomes.json ────────────────────────────────────────────────────

def test_evals_outcomes_endpoint_returns_projection_rows(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    store = _store(tmp_path)
    store.record_basket_outcome(
        report_ref="passrate-1.json", ticket="T-1", status="done",
        kind="fix_bug", verify_passed=True, pr_url="https://x/pr/1",
    )
    store.record_basket_outcome(
        report_ref="passrate-1.json", ticket="T-2", status="failed",
        kind="implement_feature", error="review rejected the change",
    )
    monkeypatch.setattr(evals_routes, "store", store)
    status, body = _get(evals_routes.evals_outcomes_json)
    assert status == 200
    assert isinstance(body, list) and len(body) == 2
    tickets = {r["ticket"] for r in body}
    assert tickets == {"T-1", "T-2"}
    done = next(r for r in body if r["ticket"] == "T-1")
    assert done["status"] == "done" and done["verify_passed"] == 1
    assert done["source"] == "basket"


def test_evals_outcomes_endpoint_honours_source_filter(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    store = _store(tmp_path)
    store.record_basket_outcome(report_ref="r.json", ticket="T-1", status="done")
    monkeypatch.setattr(evals_routes, "store", store)
    # basket rows exist; source=basket returns them, source=live returns none.
    _, basket = _get(evals_routes.evals_outcomes_json, "source=basket")
    _, live = _get(evals_routes.evals_outcomes_json, "source=live")
    assert len(basket) == 1 and basket[0]["ticket"] == "T-1"
    assert live == []


def test_evals_outcomes_endpoint_rejects_bad_source(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    monkeypatch.setattr(evals_routes, "store", _store(tmp_path))
    status, body = _get(evals_routes.evals_outcomes_json, "source=bogus")
    assert status == 400 and body["error"] == "bad_source"


def test_evals_outcomes_endpoint_rejects_bad_limit(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    monkeypatch.setattr(evals_routes, "store", _store(tmp_path))
    status, body = _get(evals_routes.evals_outcomes_json, "limit=nope")
    assert status == 400 and body["error"] == "bad_limit"


# ── /evals/cycles.json ──────────────────────────────────────────────────────

def test_evals_cycles_endpoint_empty_when_no_reports(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    # cycle_reports is bootstrapped by StateStore (PR2) but empty until a
    # window closes; the endpoint returns [] rather than 500ing.
    store = _store(tmp_path)
    monkeypatch.setattr(evals_routes, "store", store)
    status, body = _get(evals_routes.evals_cycles_json)
    assert status == 200 and body == []


def test_evals_cycles_endpoint_returns_rows_when_table_present(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    store = _store(tmp_path)
    # Record a cycle via PR2's single-writer API (not a hand-rolled CREATE —
    # StateStore already owns the DDL).
    store.record_cycle_report(
        cycle_key="2026-07-21", window_start_ms=1, window_end_ms=2,
        clean=True, wedges_json="[]", pauses_json="[]", summary="clean cycle",
        sent_at=3,
    )
    monkeypatch.setattr(evals_routes, "store", store)
    status, body = _get(evals_routes.evals_cycles_json)
    assert status == 200 and len(body) == 1
    assert body[0]["cycle_key"] == "2026-07-21" and body[0]["clean"] == 1


def test_list_cycle_reports_reraises_real_operational_error_not_missing_table(tmp_path):
    """The defensive catch degrades to [] ONLY for a genuinely-absent table —
    a real fault (locked/corrupt DB, an OperationalError that is NOT
    ``no such table``) must surface loudly, never read as an empty clean-cycle
    list (loud-failure-over-silent-degradation)."""
    store = _store(tmp_path)

    class _Boom:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    store._db = _Boom()
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        store.list_cycle_reports(limit=10)


# ── /evals/outcomes/{id}.json (single eval outcome, issue #682) ─────────────

def test_evals_outcome_detail_returns_full_row(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    store = _store(tmp_path)
    store.record_basket_outcome(
        report_ref="r.json", ticket="T-detail", status="done",
        kind="fix_bug", verify_passed=True, error=None,
    )
    rows = store.list_eval_outcomes()
    assert rows, "need at least one row to test detail"
    row_id = rows[0]["id"]
    monkeypatch.setattr(evals_routes, "store", store)
    status, body = _get_path(evals_routes.evals_outcome_detail_json, {"id": str(row_id)})
    assert status == 200
    # Wire shape: every stored field is present.
    for field in ("id", "source", "task_id", "ticket", "goal_id", "kind",
                  "workspace_dir", "status", "verify_passed", "pr_url",
                  "attempts", "wall_ms", "failure_class", "error",
                  "report_ref", "settled_at"):
        assert field in body, f"missing field: {field}"
    assert body["id"] == row_id
    assert body["ticket"] == "T-detail"
    assert body["status"] == "done"
    assert body["verify_passed"] == 1


def test_evals_outcome_detail_404_on_unknown_id(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    monkeypatch.setattr(evals_routes, "store", _store(tmp_path))
    status, body = _get_path(evals_routes.evals_outcome_detail_json, {"id": "99999"})
    assert status == 404 and body["error"] == "not_found"


def test_evals_outcome_detail_400_on_non_integer_id(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    monkeypatch.setattr(evals_routes, "store", _store(tmp_path))
    status, body = _get_path(evals_routes.evals_outcome_detail_json, {"id": "bogus"})
    assert status == 400 and body["error"] == "bad_id"


# ── /evals/cycles/{cycle_key}.json (single cycle report, issue #682) ─────────

def test_evals_cycle_detail_returns_full_row(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    store = _store(tmp_path)
    store.record_cycle_report(
        cycle_key="2026-08-25", window_start_ms=1000, window_end_ms=2000,
        clean=False, wedges_json='[{"kind":"review_crash"}]',
        pauses_json="[]", summary="one wedge", sent_at=None,
    )
    monkeypatch.setattr(evals_routes, "store", store)
    status, body = _get_path(
        evals_routes.evals_cycle_detail_json, {"cycle_key": "2026-08-25"}
    )
    assert status == 200
    # Wire shape: every stored field is present.
    for field in ("cycle_key", "window_start_ms", "window_end_ms", "clean",
                  "idle", "wedges_json", "pauses_json", "summary",
                  "sent_at", "created_at"):
        assert field in body, f"missing field: {field}"
    assert body["cycle_key"] == "2026-08-25"
    assert body["clean"] == 0
    assert body["window_start_ms"] == 1000
    assert body["window_end_ms"] == 2000
    assert body["wedges_json"] == '[{"kind":"review_crash"}]'


def test_evals_cycle_detail_404_on_unknown_key(tmp_path, monkeypatch):
    from devclaw.server.routes import evals as evals_routes
    monkeypatch.setattr(evals_routes, "store", _store(tmp_path))
    status, body = _get_path(
        evals_routes.evals_cycle_detail_json, {"cycle_key": "1999-01-01"}
    )
    assert status == 404 and body["error"] == "not_found"
