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
import pathlib

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


def test_registry_is_the_boot_guard_and_the_sandbox_set() -> None:
    """The hops read the registry, not a copy of it."""
    from devclaw import boot_guard
    from devclaw.engine import sandcastle

    assert boot_guard.REQUIRED_PRODUCTION_ENV == credentials.required_vars()
    forwarded = sandcastle._build_payload_agent_env()
    assert forwarded == list(credentials.agent_vars())


def test_every_agent_credential_crosses_the_sandbox_first() -> None:
    """A credential the agent's shell gets must also enter the container —
    ``agent`` without ``sandbox`` is a hop that cannot exist."""
    for c in credentials.REGISTRY:
        assert not (c.agent and not c.sandbox), c.var


def test_least_privilege_is_declared() -> None:
    """Every registered credential states the minimum scope it is issued with;
    a credential without a scope line cannot be granted least-privilege."""
    for c in credentials.REGISTRY:
        assert c.scope.strip(), c.var
        assert c.purpose.strip(), c.var
