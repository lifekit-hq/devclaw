"""No HTTP route may shadow a later, more specific one.

Starlette matches routes in REGISTRATION order, and registration order is the
import order of ``devclaw/server/http.py`` — which the #625 split silently
alphabetized, putting console.py's legacy catch-all redirect
``/goals/{goal_id}`` ahead of ``/goals/{goal_id}.json``: every console
goal-detail fetch matched the redirect (goal_id="….json"), 302'd into the SPA,
and the page died on "'<!doctype' is not valid JSON" (live-found 2026-08-24).

This test recomputes the full shadow matrix statically: every
``@mcp.custom_route`` pattern, in true registration order, checked pairwise —
an earlier pattern that fully matches a concrete instance of a later one is a
shadow and fails the suite. Any future route or import reorder that
reintroduces the class fails here, not in production.
"""

from __future__ import annotations

import re
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "devclaw" / "server"


def _import_order() -> list[str]:
    src = (_SERVER / "http.py").read_text()
    mods = re.findall(r"^from \.routes import (\w+)", src, re.M)
    assert mods, "http.py route imports not found — registration moved?"
    return mods


def _routes() -> list[tuple[str, frozenset[str], str]]:
    out = []
    for mod in _import_order():
        text = (_SERVER / "routes" / f"{mod}.py").read_text()
        for m in re.finditer(
            r'@mcp\.custom_route\("([^"]+)",\s*methods=\[([^\]]+)\]\)', text
        ):
            methods = frozenset(x.strip().strip("\"'") for x in m.group(2).split(","))
            out.append((m.group(1), methods, mod))
    assert len(out) > 20, f"only {len(out)} routes scanned — decorator shape changed?"
    return out


def _to_regex(pattern: str) -> re.Pattern:
    r = re.escape(pattern)
    r = re.sub(r"\\\{[^}]*:path\\\}", ".+", r)
    r = re.sub(r"\\\{[^}]*\\\}", "[^/]+", r)
    return re.compile("^" + r + "$")


def _sample(pattern: str) -> str:
    return re.sub(r"\{[^}]*\}", "x", pattern)


def test_no_route_shadows_a_later_more_specific_one():
    routes = _routes()
    shadows = []
    for i, (early, e_methods, e_mod) in enumerate(routes):
        e_re = _to_regex(early)
        for late, l_methods, l_mod in routes[i + 1:]:
            if early == late or not (e_methods & l_methods):
                continue
            if e_re.match(_sample(late)):
                shadows.append(f"{e_mod}:{early} swallows {l_mod}:{late}")
    assert not shadows, (
        "route registration order lets an earlier pattern swallow a later, "
        f"more specific one — reorder http.py's imports: {shadows}"
    )


def test_goal_json_registers_before_the_legacy_goal_redirect():
    """The concrete 2026-08-24 incident, pinned directly."""
    flat = [(p, m) for p, _, m in _routes()]
    json_idx = flat.index(("/goals/{goal_id}.json", "goals"))
    redirect_idx = flat.index(("/goals/{goal_id}", "console"))
    assert json_idx < redirect_idx
