"""Regression tests for the console configuration surfaces:

  A. ``/config/env.json`` — read-only catalog parsed live from the env-var
     reference doc. Pins the parse (finds real vars, masks secret VALUES so the
     bearer token / GH token can't leak to the browser) and the fail-soft
     (missing doc → [], never a 500).
  B. ``/projects/{id}/config`` — the editable per-project overrides. Pins that
     a set persists to the registry, ``null`` clears back to inherit, and every
     bad input (unknown field, bad enum, wrong type, unknown project) is a 4xx,
     never a silent write — secrets/infra env are unreachable here by design.
"""

from __future__ import annotations

import asyncio
import json

from starlette.requests import Request

from devclaw.project_registry import ProjectRegistry


def _req(path_params, body=None):
    scope = {"type": "http", "method": "POST", "path_params": path_params, "headers": []}

    async def receive():
        raw = json.dumps(body).encode() if body is not None else b""
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def _call(fn, req):
    resp = asyncio.run(fn(req))
    return resp.status_code, json.loads(resp.body)


def _registry(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="p", name="P", workspace_dir="/src/p")
    return reg


# ── A: env catalog ─────────────────────────────────────────────────────────

def test_env_catalog_parses_doc_and_finds_known_vars():
    from devclaw.server.routes import control as http_mod
    rows = http_mod._env_var_catalog()
    keys = {r["key"] for r in rows}
    assert "DEVCLAW_GOAL_BROWSER_GATE" in keys
    assert "DEVCLAW_MODEL_DEEP" in keys
    bg = next(r for r in rows if r["key"] == "DEVCLAW_GOAL_BROWSER_GATE")
    assert bg["group"] and bg["purpose"] and bg["default"] == "1"


def test_env_catalog_masks_secret_values(monkeypatch):
    from devclaw.server.routes import control as http_mod
    monkeypatch.setenv("DEVCLAW_TOKEN", "supersecret-bearer")
    tok = next(r for r in http_mod._env_var_catalog() if r["key"] == "DEVCLAW_TOKEN")
    assert tok["secret"] is True and tok["isSet"] is True
    assert "supersecret" not in tok["value"] and tok["value"] == "••••••"


def test_resolve_env_doc_finds_cwd_copy_under_noneditable_install(monkeypatch, tmp_path):
    # Non-editable install: the module-relative path is in site-packages WITHOUT
    # docs/, but the server runs with cwd at the repo root that has the doc. The
    # resolver must fall through to the cwd candidate rather than return a dead
    # module-relative path (the live bug: catalog came back empty in prod).
    from devclaw.server.routes import control as http_mod
    doc = tmp_path / "docs" / "reference" / "env-vars.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("## G\n| `DEVCLAW_X` | `1` | test |\n")
    # force the module-relative default to a nonexistent location
    monkeypatch.setattr(http_mod, "__file__", str(tmp_path / "pkg" / "server" / "http.py"))
    monkeypatch.chdir(tmp_path)
    assert http_mod._resolve_env_doc() == doc


def test_env_catalog_degrades_to_empty_when_doc_missing(monkeypatch, tmp_path):
    from devclaw.server.routes import control as http_mod
    monkeypatch.setattr(http_mod, "_ENV_DOC", tmp_path / "nope.md")
    assert http_mod._env_var_catalog() == []


def test_config_env_route_returns_vars():
    from devclaw.server.routes import control as http_mod
    status, body = _call(http_mod.config_env_json, _req({}))
    assert status == 200 and isinstance(body["vars"], list) and body["vars"]


# ── B: per-project overrides ───────────────────────────────────────────────

def test_project_config_get_returns_overrides(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    reg = _registry(tmp_path)
    reg.update("p", autodeploy=True)
    monkeypatch.setattr(projects_routes, "registry", reg)
    status, body = _call(projects_routes.project_config_get, _req({"project_id": "p"}))
    assert status == 200
    assert body["overrides"]["autodeploy"] is True
    assert body["overrides"]["browser_gate_mode"] is None  # unset = inherit


def test_project_config_get_404_unknown(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, _ = _call(projects_routes.project_config_get, _req({"project_id": "nope"}))
    assert status == 404


def test_project_config_set_persists_and_clears(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    reg = _registry(tmp_path)
    monkeypatch.setattr(projects_routes, "registry", reg)
    status, body = _call(
        projects_routes.project_config_set,
        _req({"project_id": "p"}, {"autodeploy": True, "browser_gate_mode": "strict"}),
    )
    assert status == 200
    assert body["overrides"]["autodeploy"] is True
    assert body["overrides"]["browser_gate_mode"] == "strict"
    assert reg.get("p").autodeploy is True and reg.get("p").browser_gate_mode == "strict"
    # null clears back to inherit
    status, body = _call(projects_routes.project_config_set, _req({"project_id": "p"}, {"autodeploy": None}))
    assert status == 200 and body["overrides"]["autodeploy"] is None
    assert reg.get("p").autodeploy is None


def test_project_config_set_rejects_unknown_field(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, body = _call(projects_routes.project_config_set, _req({"project_id": "p"}, {"anthropic_api_key": "sk-x"}))
    assert status == 400 and body["error"] == "unknown_field"


def test_project_config_set_rejects_bad_enum(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, body = _call(projects_routes.project_config_set, _req({"project_id": "p"}, {"browser_gate_mode": "octopus"}))
    assert status == 400 and body["error"] == "bad_value"


def test_project_config_set_rejects_wrong_type(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, body = _call(projects_routes.project_config_set, _req({"project_id": "p"}, {"autodeploy": "yes"}))
    assert status == 400 and body["error"] == "bad_value"


def test_project_config_set_empty_patch_is_400(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, body = _call(projects_routes.project_config_set, _req({"project_id": "p"}, {}))
    assert status == 400 and body["error"] == "empty_patch"


def test_project_config_set_404_unknown(tmp_path, monkeypatch):
    from devclaw.server.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "registry", _registry(tmp_path))
    status, _ = _call(projects_routes.project_config_set, _req({"project_id": "nope"}, {"autodeploy": True}))
    assert status == 404
