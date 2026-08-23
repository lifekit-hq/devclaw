"""devclaw/config.py is the single doorway for DEVCLAW_* configuration.

Structural guard (ast-walk, same style as test_extraction_boundaries): no
module in the devclaw package other than ``config.py`` may read a
``DEVCLAW_*`` environment variable. Before the doorway existed the same ~60
variables were read ad-hoc across ~30 modules — two files parsed DEVCLAW_DB
and DEVCLAW_GOALS_DIR independently with their own defaults, the
same-fact-computed-twice drift class (#630). This test keeps the sprawl from
creeping back.

Deliberately allowed:
- ``config.py`` itself — the doorway.
- ``_env_loader.py`` — the DEVCLAW_DOTENV bootstrap that must run before any
  config exists.
- ``runner/`` — the in-sandbox worker is standalone by design (spec 011) and
  is not scanned.
- Reads through a *variable* key (the console's env-catalog introspection in
  server/routes/control.py reports the raw environment on purpose).

Env *writes* (child-env composition, the OAuth key-stripping) are not reads
and are not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "devclaw"
_ALLOWED = {_PKG / "config.py", _PKG / "_env_loader.py"}


def _devclaw_env_reads(tree: ast.AST) -> list[str]:
    """DEVCLAW_* literals used as the key of an os.environ/os.getenv read."""
    hits: list[str] = []

    def _is_environ(node: ast.AST) -> bool:
        # os.environ / environ (from-import)
        return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
            isinstance(node, ast.Name) and node.id == "environ"
        )

    for node in ast.walk(tree):
        key: ast.AST | None = None
        if isinstance(node, ast.Call):
            f = node.func
            # os.environ.get / os.environ.setdefault / os.getenv / getenv
            if isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault") and _is_environ(f.value):
                key = node.args[0] if node.args else None
            elif (isinstance(f, ast.Attribute) and f.attr == "getenv") or (
                isinstance(f, ast.Name) and f.id == "getenv"
            ):
                key = node.args[0] if node.args else None
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            key = node.slice
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and key.value.startswith("DEVCLAW_")
        ):
            hits.append(key.value)
    return hits


def test_only_config_reads_devclaw_env_vars():
    offenders: dict[str, list[str]] = {}
    for path in sorted(_PKG.rglob("*.py")):
        if path in _ALLOWED or "console_dist" in path.parts:
            continue
        reads = _devclaw_env_reads(ast.parse(path.read_text(encoding="utf-8")))
        if reads:
            offenders[str(path.relative_to(_REPO))] = sorted(set(reads))
    assert not offenders, (
        "DEVCLAW_* env reads outside the devclaw/config.py doorway — move the "
        f"read into config.py (one home, one default, one parse): {offenders}"
    )


def test_the_doorway_itself_reads_devclaw_env_vars():
    """Sanity: the scanner actually detects reads (a broken matcher would make
    the guard above vacuously green)."""
    reads = _devclaw_env_reads(ast.parse((_PKG / "config.py").read_text(encoding="utf-8")))
    assert len(set(reads)) > 40, f"scanner found only {len(set(reads))} reads in config.py"
