"""docs/reference/env-vars.md ↔ code parity — the env surface stays honest.

The doc calls itself the single source of truth; history shows it drifts
(pre-2026-07-11 it documented ~60 of ~85 vars actually read). This test makes
drift a failure instead of an archaeology project:

  - every ``DEVCLAW_*`` var the runtime reads must have a row in the doc;
  - every documented row must correspond to a real read in the code.

Scope is the runtime: the ``devclaw`` package. Test
fixtures (``DEVCLAW_TEST_*`` in tests/), the offline eval harness
(``MEASURE_*`` in evals/), and Claude-harness hooks (.claude/) are not env
surface and are deliberately outside the scan.
"""

from __future__ import annotations

import re
from pathlib import Path

from devclaw import credentials

_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "docs" / "reference" / "env-vars.md"

#: an env READ: os.environ.get / [] / setdefault / os.getenv, tolerating a
#: line break between the call and the var-name literal.
_READ_RE = re.compile(
    r'(?:environ(?:\.get|\.setdefault)?|getenv)\s*[\(\[]\s*"(DEVCLAW_[A-Z_]+)"'
)
_ROW_RE = re.compile(r"^\| `(DEVCLAW_[A-Z_]+)`", re.MULTILINE)

_COMPOSE = _REPO / "deploy" / "docker-compose.devclaw.yml"
_ENGINE = _REPO / "devclaw" / "engine" / "claude.py"
# The engine stamps the session's git identity through this module, so its
# config reads are engine dials too (DEVCLAW_GIT_NAME/EMAIL were documented,
# read, and inert in production until 2026-09-06 because the scan stopped at
# the launcher).
_GIT_IDENTITY = _REPO / "devclaw" / "git_identity.py"
_WORKSPACE = _REPO / "devclaw" / "engine" / "workspace.py"
_CONFIG = _REPO / "devclaw" / "config.py"
#: a `_config.<NAME>` reference in the engine
_CFG_REF_RE = re.compile(r"_config\.([A-Za-z_]+)")
#: `NAME = os.environ.get("DEVCLAW_X"` or a `def name()` whose body reads one
_CFG_BIND_RE = re.compile(
    r"^(?:def\s+)?([A-Za-z_]+)\s*(?:\(\)[^\n]*)?=?[^\n]*\n(?:[^\n]*\n){0,8}?",
    re.MULTILINE,
)


def _runtime_reads() -> set[str]:
    files = list((_REPO / "devclaw").rglob("*.py"))
    assert files, "runtime source not found — repo layout changed?"
    reads: set[str] = {c.var for c in credentials.REGISTRY if c.var.startswith("DEVCLAW_")}
    for f in files:
        reads |= set(_READ_RE.findall(f.read_text(encoding="utf-8")))
    return reads


def _documented_rows() -> set[str]:
    return set(_ROW_RE.findall(_DOC.read_text(encoding="utf-8")))


def test_every_env_read_is_documented():
    undocumented = _runtime_reads() - _documented_rows()
    assert not undocumented, (
        f"env vars read by the runtime but missing from docs/reference/env-vars.md: "
        f"{sorted(undocumented)} — add a row (or demote the read to a "
        f"constant if it isn't a real per-host fact)"
    )


def test_every_documented_var_is_read():
    ghosts = _documented_rows() - _runtime_reads()
    assert not ghosts, (
        f"vars documented in docs/reference/env-vars.md but read nowhere in the "
        f"runtime: {sorted(ghosts)} — remove the row (dead config docs are "
        f"worse than none)"
    )


def _engine_env_vars() -> set[str]:
    """Every ``DEVCLAW_*`` var the ENGINE acts on.

    Resolved structurally rather than from a hand-kept list: collect the
    ``_config.<NAME>`` references in ``engine/claude.py``, then map each
    back to the env var its binding in ``config.py`` reads. A new engine dial
    is therefore covered the moment the engine reads it — no list to remember.
    """
    src = "".join(p.read_text(encoding="utf-8") for p in (_ENGINE, _GIT_IDENTITY, _WORKSPACE))
    names = set(_CFG_REF_RE.findall(src))
    config_src = _CONFIG.read_text(encoding="utf-8")
    found: set[str] = set()
    for name in names:
        # module-level constant: NAME = os.environ.get("DEVCLAW_X", ...)
        m = re.search(
            rf"^{re.escape(name)}\s*=\s*[^\n]*?\"(DEVCLAW_[A-Z_]+)\"",
            config_src, re.MULTILINE,
        )
        if m:
            found.add(m.group(1))
            continue
        # accessor: def name() -> ...:  <body reads DEVCLAW_X>
        m = re.search(
            rf"^def\s+{re.escape(name)}\s*\([^)]*\)[^\n]*:\n(?:(?!^def\s).*\n)*?"
            rf".*?\"(DEVCLAW_[A-Z_]+)\"",
            config_src, re.MULTILINE,
        )
        if m:
            found.add(m.group(1))
    return found


def test_every_engine_dial_reaches_the_deployed_container():
    """A dial the engine reads must be forwarded by the production
    compose file — otherwise it is documented, settable, and silently inert.

    The compose file forwards ONLY the vars it names; its one ``env_file`` is
    the secrets file (the two credentials, no ``DEVCLAW_*`` dial). So a knob an
    operator sets in ``/srv/devclaw/.env`` never reaches the process unless a
    line exists here. That trap is called out in the compose
    file itself for ``DEVCLAW_MAX_CONCURRENT`` — and was fixed for that one var
    only. ``DEVCLAW_EXEC_MODEL`` (documented as "the token/quota bulk") was
    among five that stayed inert, so the deployed worker could not be moved off
    the default model at all. This pins the CLASS: any var the engine reads.
    """
    compose = _COMPOSE.read_text(encoding="utf-8")
    forwarded = set(re.findall(r"^\s{6}(DEVCLAW_[A-Z_]+):", compose, re.MULTILINE))
    dials = _engine_env_vars()
    assert dials, "structural scan found no engine dials — the regex rotted"
    missing = sorted(dials - forwarded)
    assert not missing, (
        "these engine dials are read by engine/claude.py but NOT forwarded "
        f"by deploy/docker-compose.devclaw.yml, so setting them on the deployed "
        f"instance silently does nothing: {missing}"
    )
