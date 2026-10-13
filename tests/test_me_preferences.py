"""Tripwire: the console's per-user appearance is keyed by the gate identity and
fails closed. No identity header -> 401 and no row touched; an invalid
appearance is a 400 that writes nothing; two identities never share a row; and
the new table lands on an existing database without disturbing its rows."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from starlette.datastructures import Headers

from devclaw.server.routes import me
from devclaw.state_store import StateStore


class _Request:
    def __init__(self, headers: dict | None = None, body=None) -> None:
        self.headers = Headers(headers or {})
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = StateStore(str(tmp_path / "t.db"))
    monkeypatch.setattr(me, "store", s)
    yield s
    s.close()


def _get(headers=None):
    return asyncio.run(me.me_preferences(_Request(headers)))


def _patch(body, headers=None):
    return asyncio.run(me.me_preferences_patch(_Request(headers, body)))


def _json(resp):
    return json.loads(resp.body)


ALICE = {"X-Auth-Request-Email": "Alice@Example.com "}
BOB = {"X-Auth-Request-Email": "bob@example.com"}
DARK = {"appearance": {"theme": "dark", "seed": "#AABBCC", "intensity": 0.5}}


def test_no_identity_is_401_and_touches_nothing(store):
    assert _get().status_code == 401
    assert _patch(DARK).status_code == 401
    assert store._db.execute("SELECT COUNT(*) FROM user_preferences").fetchone()[0] == 0


def test_identity_keys_the_row_and_is_isolated(store):
    assert _json(_get(ALICE)) == {"appearance": {"theme": "system"}}
    resp = _patch(DARK, ALICE)
    assert resp.status_code == 200
    assert _json(resp) == {"appearance": {"theme": "dark", "seed": "#aabbcc", "intensity": 0.5}}
    # the same person under a differently-cased header is the same row
    assert _json(_get({"X-Auth-Request-Email": "alice@example.com"})) == _json(resp)
    assert _json(_get(BOB)) == {"appearance": {"theme": "system"}}


def test_a_present_appearance_replaces_wholesale_and_absent_is_untouched(store):
    _patch(DARK, ALICE)
    assert _json(_patch({}, ALICE))["appearance"]["theme"] == "dark"
    assert _json(_patch({"appearance": {"theme": "light"}}, ALICE)) == {
        "appearance": {"theme": "light"}}


@pytest.mark.parametrize("body", [
    {"appearance": {"theme": "sepia"}},
    {"appearance": {}},
    {"appearance": {"theme": "dark", "seed": "red"}},
    {"appearance": {"theme": "dark", "intensity": 2}},
    {"appearance": {"theme": "dark", "intensity": True}},
    {"appearance": "dark"},
    ["not", "an", "object"],
    ValueError("bad json"),
])
def test_invalid_input_is_400_and_writes_nothing(store, body):
    _patch(DARK, ALICE)
    resp = _patch(body, ALICE)
    assert resp.status_code == 400
    assert _json(_get(ALICE))["appearance"]["theme"] == "dark"


def test_the_table_is_additive_on_an_existing_database(tmp_path):
    path = str(tmp_path / "old.db")
    first = StateStore(path)
    first.set_meta("operator_hold", "kept")
    first._db.execute("DROP TABLE user_preferences")
    first._db.commit()
    first.close()
    second = StateStore(path)  # bootstrap re-runs over the pre-table database
    try:
        assert second.get_meta("operator_hold") == "kept"
        second.set_appearance("a@b.c", "dark", None, None)
        assert second.get_appearance("a@b.c") == {"theme": "dark", "seed": None, "intensity": None}
    finally:
        second.close()
    sqlite3.connect(path).close()
