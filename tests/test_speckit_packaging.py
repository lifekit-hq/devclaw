"""The vendored speckit scaffold must reach an INSTALLED devclaw (#588).

`.specify/` sits at the repo root, outside `packages = ["devclaw"]`, so the
wheel never carried it. A source checkout kept working by accident — there the
scaffold happens to sit beside the package dir — so the whole test suite stayed
green while `onboard` raised "cannot install speckit: vendored source
/usr/local/lib/python3.13/dist-packages/.specify is missing" in every deployed
container. These tests therefore assert the PACKAGING, not just the checkout
path: revert the pyproject force-include and
``test_vendored_scaffold_is_force_included_into_the_wheel`` fails.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from devclaw.speckit_setup import (
    _SCAFFOLD_DIRS,
    _SCAFFOLD_FILES,
    _resolve_speckit_source,
    _speckit_source,
)

if sys.version_info < (3, 11):  # pragma: no cover - project requires >=3.11
    raise RuntimeError("tomllib requires Python 3.11+")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGED_PREFIX = "devclaw/_specify"


def _force_include() -> dict[str, str]:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    return wheel.get("force-include", {})


def test_vendored_scaffold_is_force_included_into_the_wheel() -> None:
    """Every scaffold part the installer copies must be mapped INSIDE the
    package, or an installed devclaw cannot onboard anything."""
    mapping = _force_include()
    assert mapping, (
        "pyproject declares no wheel force-include: the vendored .specify/ "
        "scaffold will not ship in the wheel and onboard breaks once installed"
    )
    for name in (*_SCAFFOLD_DIRS, *_SCAFFOLD_FILES):
        src = f".specify/{name}"
        assert src in mapping, f"{src} is not force-included into the wheel"
        assert mapping[src] == f"{_PACKAGED_PREFIX}/{name}", (
            f"{src} must land inside the devclaw package so the installed "
            f"resolver finds it; got {mapping[src]!r}"
        )


def test_resolver_prefers_the_packaged_copy_when_installed(tmp_path: Path) -> None:
    """Installed layout: the scaffold sits inside the package."""
    pkg = tmp_path / "site-packages" / "devclaw"
    (pkg / "_specify").mkdir(parents=True)
    assert _resolve_speckit_source(pkg) == pkg / "_specify"


def test_resolver_falls_back_to_repo_root_in_a_source_checkout(tmp_path: Path) -> None:
    """Checkout layout: no packaged copy, scaffold is the root sibling."""
    pkg = tmp_path / "repo" / "devclaw"
    pkg.mkdir(parents=True)
    (tmp_path / "repo" / ".specify").mkdir()
    assert _resolve_speckit_source(pkg) == tmp_path / "repo" / ".specify"


def test_vendored_scaffold_resolves_and_carries_every_part_it_copies() -> None:
    """Whichever layout this test runs in, the resolved source must exist and
    hold every part `scaffold_specify` copies out of it."""
    src = _speckit_source()
    assert src.is_dir(), f"vendored speckit source missing at {src}"
    for name in _SCAFFOLD_DIRS:
        assert (src / name).is_dir(), f"{name}/ missing from {src}"
    for name in _SCAFFOLD_FILES:
        assert (src / name).is_file(), f"{name} missing from {src}"
    assert (src / "templates" / "constitution-template.md").is_file()
