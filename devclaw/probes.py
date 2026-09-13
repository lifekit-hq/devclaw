"""Credential liveness probes — the ONE way devclaw asks "does this
credential work right now". Never raise, never log or return a value; every
failure degrades to ``None`` (unknown), never to green."""

from __future__ import annotations

import os
from typing import Mapping, Optional

from . import credentials as _credentials


def probe_github_token(token: str, timeout_s: float = 5.0) -> Optional[int]:
    """HTTP status of an authenticated GitHub API call, or None."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "devclaw"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:  # noqa: BLE001
        return None


def green_credentials(environ: Optional[Mapping[str, str]] = None) -> tuple[str, ...]:
    """The registry credentials that are present, well-formed and — for the
    GitHub-shaped ones — answer 200 right now. Part of a goal's world
    fingerprint (spec 046): adding a credential wakes a goal blocked on it."""
    env = os.environ if environ is None else environ
    green: list[str] = []
    for cred in _credentials.REGISTRY:
        value = (env.get(cred.var) or "").strip()
        if not value or not _credentials.well_formed(cred, value):
            continue
        if cred.prefixes:
            if probe_github_token(value) != 200:
                continue
        green.append(cred.var)
    return tuple(green)
