"""Two modules are kept extraction-ready, and this file pins it structurally.

``devclaw/loom/`` is the engine-agnostic substrate — its own docstring says it
imports nothing from the rest of devclaw, so it can someday be lifted out
whole. ``runner/acp_client.py`` is the runner's zero-dependency ACP client
(spec 011): importable and testable with no SDK installed, so the agent-drive
seam stays the only thing that changes when the agent is swapped.

Neither property is enforced by anything at runtime — a stray
``from devclaw import cognition`` inside loom, or a convenience third-party
import inside the ACP client, works fine today and silently welds the module
to the host package, and the extraction-readiness is gone with no failing
test. Same guard style as ``tests/test_views_never_read_back.py``: parse the
source with ``ast`` and refuse the import class, not one instance of it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _imports(path: Path):
    """Yield ``(module, level, lineno)`` for every import statement in a file.

    ``level`` is 0 for absolute imports; for ``from . import x`` /
    ``from ..pkg import y`` it is the number of leading dots.
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0, node.lineno
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level, node.lineno


def test_loom_imports_nothing_from_devclaw_outside_loom():
    """Loom is the extraction-ready substrate — a single import of
    ``devclaw.<anything-but-loom>`` (absolute, or a relative import reaching
    above the loom package) welds it to the host package and the claim in its
    own ``__init__`` docstring becomes a lie nothing checks."""
    loom_files = sorted((ROOT / "devclaw" / "loom").rglob("*.py"))
    assert loom_files, "devclaw/loom moved — update this guard, don't delete it"

    offenders: list[str] = []
    for path in loom_files:
        rel = path.relative_to(ROOT).as_posix()
        # the package the file lives in, e.g. ("devclaw", "loom")
        pkg = path.relative_to(ROOT).parent.parts
        for module, level, lineno in _imports(path):
            if level == 0:
                resolved = module
            elif level - 1 >= len(pkg):
                offenders.append(f"{rel}:{lineno} relative import escapes the repo root")
                continue
            else:
                base = pkg[: len(pkg) - (level - 1)]
                resolved = ".".join(base + ((module,) if module else ()))
            if (resolved == "devclaw" or resolved.startswith("devclaw.")) and not (
                resolved == "devclaw.loom" or resolved.startswith("devclaw.loom.")
            ):
                offenders.append(f"{rel}:{lineno} imports {resolved}")
    assert offenders == [], (
        "devclaw/loom is extraction-ready — it must import nothing from "
        f"devclaw outside devclaw.loom, but: {offenders}"
    )


def test_acp_client_imports_only_the_stdlib():
    """The ACP client is zero-dependency by design (spec 011): the one
    protocol library ever pinned transitively already broke every sandbox
    turn once. Any non-stdlib import — a package, a sibling runner module, a
    devclaw module — quietly re-creates that dependency surface."""
    path = ROOT / "runner" / "acp_client.py"
    assert path.exists(), "runner/acp_client.py moved — update this guard, don't delete it"

    offenders: list[str] = []
    for module, level, lineno in _imports(path):
        if level > 0:
            offenders.append(f"line {lineno}: relative import ({'.' * level}{module})")
            continue
        top = module.split(".", 1)[0]
        if top not in sys.stdlib_module_names:
            offenders.append(f"line {lineno}: imports {module}")
    assert offenders == [], (
        "runner/acp_client.py is the deliberately zero-dependency ACP client "
        f"(spec 011) — Python stdlib only, but: {offenders}"
    )
