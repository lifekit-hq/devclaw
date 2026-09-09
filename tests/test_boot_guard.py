"""Fail-closed at boot — the production engine never starts without its
credentials (tinyspec durable-container-secrets, 2026-09-04).

Tripwire class: fail-closed. On 2026-09-03 a hand recreate produced a
container with both credentials blank; it answered /health for ~20 hours,
ran cognition on the revocable mounted login, and burned a worker session on
an `npm ci` 401. A container without its credentials must never run at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devclaw import boot_guard, credentials

_REPO = Path(__file__).resolve().parents[1]

# Realistic-shaped dummies — the assertion that they never leak is the point.
# DERIVED from the registry (spec 042): a hand-written pair is exactly the
# per-hop list the registry replaced, and it would go on passing while a newly
# required credential had no coverage here at all.
_REQUIRED = {
    c.var: ("ghp_dummyvalueneverechoed" if c.prefixes
            else "sk-ant-oat01-dummy-value-never-echoed")
    for c in credentials.REGISTRY if c.required
}


def test_the_required_set_is_the_registrys():
    assert tuple(_REQUIRED) == boot_guard.REQUIRED_PRODUCTION_ENV
    assert len(_REQUIRED) >= 2  # the fixture is not vacuous


@pytest.mark.parametrize("missing", sorted(_REQUIRED))
@pytest.mark.parametrize("blank", ["", "   "])
def test_production_refuses_without_a_required_credential(missing, blank):
    """Blank and absent are the same thing; the refusal names the variable
    and the fix and never a value."""
    env = dict(_REQUIRED)
    env[missing] = blank
    with pytest.raises(SystemExit) as exc:
        boot_guard.assert_required_env(env, engine="")
    msg = str(exc.value)
    assert exc.value.code == msg  # a message, i.e. non-zero exit, not `SystemExit(0)`
    assert "refuses to start" in msg and missing in msg
    assert "deploy" in msg  # the fix is named
    for value in _REQUIRED.values():
        assert value not in msg


def test_production_refuses_when_all_are_absent():
    with pytest.raises(SystemExit) as exc:
        boot_guard.assert_required_env({}, engine="")
    msg = str(exc.value)
    assert all(name in msg for name in _REQUIRED)


def test_production_starts_with_every_credential_set():
    boot_guard.assert_required_env(dict(_REQUIRED), engine="")  # no raise


@pytest.mark.parametrize("engine", ["host", "stub"])
def test_dev_engines_require_no_credentials(engine):
    assert boot_guard.required_env(engine) == ()
    boot_guard.assert_required_env({}, engine=engine)  # no raise


def test_entrypoint_runs_the_guard_before_anything_serves():
    """Structural: the server entrypoint calls the guard before crash
    recovery and before either transport serves — a guard that runs after
    the first side effect is not a boot guard."""
    src = (_REPO / "devclaw" / "server" / "lifecycle.py").read_text(encoding="utf-8")
    main_body = src[src.index("def main()"):]
    guard = main_body.index("assert_required_env()")
    assert guard < main_body.index("queue.recover()")
    assert guard < main_body.index("_serve_stdio()")
    assert guard < main_body.index("_serve_http()")
