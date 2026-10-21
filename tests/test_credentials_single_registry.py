"""Structural tripwire (OAuth strip + sandbox fence class): a credential has
ONE spelling and ONE home — ``devclaw/credentials.py`` (spec 042).

A credential crosses eight hops (Actions secret → deploy → secrets file →
compose → container → ``docker run -e`` → runner allowlist → agent shell), and
before the registry every hop named it by hand. Each credential was added to
some hops and not others; the miss surfaced at the last hop as a worker's
prose — the OAuth setup-token on 2026-08-24 (#644) and the registry token on
2026-09-08, same hop, five weeks apart. This guard reads the source so a
credential name typed anywhere else in the package fails the build before it
can become a hop nobody wired.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from devclaw import credentials

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "devclaw"
_HOME = _PKG / "credentials.py"
_NAMES = {c.var for c in credentials.REGISTRY} | set(credentials.REFUSED)


def _string_literals(path: pathlib.Path) -> set[str]:
    """Every string constant that is code, not documentation: docstrings and
    bare-expression strings are prose and may name a credential."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            doc_nodes.add(id(node.value))
    return {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes
    }


def test_no_credential_name_is_spelled_outside_the_registry() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _PKG.rglob("*.py"):
        if path == _HOME:
            continue
        hit = _string_literals(path) & _NAMES
        if hit:
            offenders[str(path.relative_to(_ROOT))] = hit
    assert not offenders, (
        "a credential is named outside devclaw/credentials.py — add the hop to the "
        f"registry, never a hand-written name: {offenders}"
    )


def test_registry_is_the_boot_guard_and_the_session_env(monkeypatch) -> None:
    """The hops read the registry, not a copy of it: the session keeps the
    agent credentials and loses every host-only one and the metered keys."""
    from devclaw import boot_guard
    from devclaw.engine import claude

    assert boot_guard.REQUIRED_PRODUCTION_ENV == credentials.required_vars()
    for var in (*(c.var for c in credentials.REGISTRY), *credentials.REFUSED):
        monkeypatch.setenv(var, "x")
    env = claude.session_env()
    registered = {c.var for c in credentials.REGISTRY} | set(credentials.REFUSED)
    assert {k for k in env if k in registered} == set(credentials.agent_vars())


@pytest.mark.parametrize("var", [
    "DEVCLAW_TOKEN", "DEVCLAW_GOAL_NOTIFY_URL", "GH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
])
def test_host_only_secret_never_reaches_the_session_or_its_verify(monkeypatch, tmp_path, var) -> None:
    """The worktree is not a sandbox, so the env strip is the boundary: the
    MCP bearer token, the notify webhook, the GitHub token and the metered
    keys must be absent from both the ``claude -p`` env and the verify run."""
    from devclaw.engine import claude

    monkeypatch.setenv(var, "placeholder")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "placeholder")
    assert var not in claude.session_env()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in claude.session_env()
    result = asyncio.run(claude.run_verify("env | cut -d= -f1", str(tmp_path), timeout=30))
    assert result["passed"], result
    names = set(result["output"].split())
    assert "CLAUDE_CODE_OAUTH_TOKEN" in names
    assert var not in names


@pytest.mark.parametrize("var", ["DEVCLAW_TOKEN", "DEVCLAW_GOAL_NOTIFY_URL"])
def test_a_host_only_optional_secret_never_moves_the_fingerprint(var) -> None:
    """Setting or rotating the MCP token or the notify URL must not wake every
    open goal: the world fingerprint's credential set ignores them."""
    from devclaw.probes import green_credentials

    base = {"CLAUDE_CODE_OAUTH_TOKEN": "placeholder"}
    assert green_credentials(base) == green_credentials({**base, var: "placeholder"}) == ("CLAUDE_CODE_OAUTH_TOKEN",)


def test_least_privilege_is_declared() -> None:
    """Every registered credential states the minimum scope it is issued with;
    a credential without a scope line cannot be granted least-privilege."""
    for c in credentials.REGISTRY:
        assert c.scope.strip(), c.var
        assert c.purpose.strip(), c.var
