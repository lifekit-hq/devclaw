"""P2b (host-side half): the ``gh`` READ adapter for the wayfinder plan-map.

`docs/proposals/cognition-demolition.md`. The control plane READS the worker-owned
map off the target repo's issue tracker. This module is the thin host-side adapter
that turns ``gh issue list`` output into the normalized issue dicts
:func:`devclaw.goal.wayfinder.parse_map` consumes — nothing more. The WRITE side
(the worker creating/closing map issues via ``gh``, inside the sandbox) and the
light pull-brief at ``openhands-runner/runner.py`` are P2b's *sandbox* half,
validated live at the shakedown/baseline; this host-read half is pure-enough to
unit-test by injecting the subprocess.

**Fail LOUD, not silent.** A gh failure (non-zero exit, unparseable output) raises
:class:`WayfinderGhError` so the caller can fall back to the cached last-known map
or block legibly (#185/#188). It must NEVER be swallowed into an empty list —
``parse_map`` would read that as "no map / not charted" and wrongly drive a goal
that actually HAS a map. Empty ``[]`` is returned ONLY when gh succeeds with zero
issues.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, Optional

from .wayfinder import WayfinderMap, parse_map

#: the gh JSON fields parse_map needs.
_JSON_FIELDS = "number,title,body,state,labels"
#: bound on issues pulled. devclaw's target repos are small, and ``parse_map``
#: filters to wayfinder-labelled issues — so a plain list (no label search) is
#: correct and avoids depending on gh's multi-label search dialect.
_DEFAULT_LIMIT = 200

#: injected subprocess runner (tests stub it); matches ``subprocess.run``.
Runner = Callable[..., "subprocess.CompletedProcess"]


class WayfinderGhError(Exception):
    """A gh read failed (non-zero exit or unparseable output). Loud on purpose:
    the caller caches-fallback or blocks legibly; this is never read as "no map"."""


def _gh_argv(repo: str, limit: int) -> list[str]:
    return [
        "gh", "issue", "list", "--repo", repo, "--state", "all",
        "--limit", str(limit), "--json", _JSON_FIELDS,
    ]


def fetch_map_issues(
    repo: str, *, limit: int = _DEFAULT_LIMIT, run: Runner = subprocess.run,
) -> list[dict]:
    """Return the target repo's issues as normalized dicts (raw material for
    :func:`parse_map`). ``run`` is injected so tests stub the subprocess. Raises
    :class:`WayfinderGhError` on a gh failure or unparseable output — it never
    returns ``[]`` to mean "gh broke" (only "gh succeeded, zero issues")."""
    argv = _gh_argv(repo, limit)
    try:
        proc = run(argv, capture_output=True, text=True)
    except (OSError, ValueError) as exc:  # gh missing / bad invocation
        raise WayfinderGhError(f"gh invocation failed for {repo}: {exc}") from exc
    if proc.returncode != 0:
        raise WayfinderGhError(
            f"gh issue list failed for {repo} (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        return []  # some gh versions print nothing for an empty list
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise WayfinderGhError(
            f"gh returned unparseable JSON for {repo}: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise WayfinderGhError(
            f"gh returned non-list JSON for {repo}: {type(data).__name__}"
        )
    return data


def read_map(
    repo: str, *, limit: int = _DEFAULT_LIMIT, run: Runner = subprocess.run,
) -> Optional[WayfinderMap]:
    """Fetch + parse the target repo's wayfinder map. Returns ``None`` when gh
    succeeded but no ``wayfinder:map`` issue exists (genuinely not charted).
    Propagates :class:`WayfinderGhError` on a gh failure — the caller decides
    cache-fallback vs block-legibly; this adapter never guesses."""
    return parse_map(fetch_map_issues(repo, limit=limit, run=run))
