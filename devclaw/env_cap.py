"""Environment-capability probes — spec 030 FR-001/FR-004/FR-006.

A capability probe is a zero-LLM check with a stable id (e.g.
``registry:npm-github``) that produces green/red/unknown + evidence. Results
are TTL-cached in the state-store meta table; the sweep runner refreshes them
at most once per heartbeat sweep (``PROBE_TTL_S``), and only when at least one
registered project has declared the capability in its ``devclaw.json``. The
per-goal dispatch gate reads the persisted rows — it never probes networks.

v1 capability set (FR-006):
- ``registry:npm-github`` — the GitHub Packages npm-registry credential is
  present and accepted by GitHub (the fs-479 class). Reference implementation:
  ``devclaw.doctor.checks_instance.check_registry_token``.
- ``sandbox:image`` — the per-task sandbox Docker image is present/pullable
  (a missing image burns sessions identically to a bad token).

FR-007: a probe that cannot RUN (infra failure, daemon unavailable) returns
``unknown`` and does NOT hold dispatch — fail-open on uncertainty,
fail-closed on evidence of breakage (a red result IS evidence).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional

from .engine.sandcastle import REGISTRY_TOKEN_VAR as _REGISTRY_TOKEN_VAR
from .engine.sandcastle import SANDBOX_IMAGE as _SANDBOX_IMAGE
from .doctor.checks_instance import (
    _probe_registry_token as _ci_probe_registry_token,
    _GH_TOKEN_PREFIXES as _CI_GH_PREFIXES,
)

if TYPE_CHECKING:
    from .state_store import StateStore

# ---- constants ---------------------------------------------------------------

#: TTL for a cached probe result. Slightly wider than the ~15-min heartbeat so
#: a result from one sweep is still valid when the next sweep reads it.
PROBE_TTL_S: int = 16 * 60  # 16 min

_META_PREFIX = "env_cap_probe:"

#: v1 capability ids this instance can probe (FR-006).
KNOWN_CAPABILITIES: frozenset[str] = frozenset({"registry:npm-github", "sandbox:image"})

# ---- data types --------------------------------------------------------------

CapStatus = Literal["green", "red", "unknown"]


@dataclass(frozen=True)
class CapProbeResult:
    """The outcome of one capability probe run."""
    status: CapStatus
    evidence: str = ""
    #: Human-readable fix action; non-empty only when status is ``red``.
    remedy: str = ""


# ---- meta-table helpers ------------------------------------------------------

def _meta_key(cap_id: str) -> str:
    return f"{_META_PREFIX}{cap_id}"


def read_result(store: "StateStore", cap_id: str) -> Optional[CapProbeResult]:
    """Read the last persisted probe result for ``cap_id``.

    Returns ``None`` if the capability has never been probed.  The admission
    gate treats ``None`` as ``unknown``, which is fail-open (FR-007)."""
    raw = store.get_meta(_meta_key(cap_id))
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return CapProbeResult(
            status=d.get("status", "unknown"),
            evidence=d.get("evidence", ""),
            remedy=d.get("remedy", ""),
        )
    except Exception:  # noqa: BLE001
        return None


def _write_result(store: "StateStore", cap_id: str, result: CapProbeResult) -> None:
    from .state_store import _now_ms  # deferred — avoids circular at module load
    raw = json.dumps({
        "status": result.status,
        "evidence": result.evidence,
        "remedy": result.remedy,
        "probed_at_ms": _now_ms(),
    })
    store.set_meta(_meta_key(cap_id), raw)


def _is_stale(store: "StateStore", cap_id: str) -> bool:
    """True when there is no cached result or it is older than ``PROBE_TTL_S``."""
    raw = store.get_meta(_meta_key(cap_id))
    if not raw:
        return True
    try:
        d = json.loads(raw)
        from .state_store import _now_ms
        age_ms = _now_ms() - int(d.get("probed_at_ms", 0))
        return age_ms > PROBE_TTL_S * 1000
    except Exception:  # noqa: BLE001
        return True


# ---- probe implementations (module-level for test patching) ------------------

def _probe_registry_npm_github() -> CapProbeResult:
    """Probe the GitHub Packages npm-registry credential (the fs-479 class).

    Reuses the same HTTP check as
    ``devclaw.doctor.checks_instance._probe_registry_token`` — the reference
    implementation from spec FR-006. Module-level so tests can patch it.
    Never raises; returns ``unknown`` on infra failure (FR-007)."""
    token = os.environ.get(_REGISTRY_TOKEN_VAR, "").strip()
    if not token:
        # Not set: supported posture (pre-token deployment), not evidence of
        # breakage — admit the goal (mirrors check_registry_token's reasoning).
        return CapProbeResult(
            status="green",
            evidence=f"{_REGISTRY_TOKEN_VAR} not set — no registry credential; "
                     "npm ci will only succeed against public registries",
        )
    if not token.startswith(_CI_GH_PREFIXES):
        return CapProbeResult(
            status="red",
            evidence=(
                f"{_REGISTRY_TOKEN_VAR} is set but is not a GitHub token "
                f"(expected prefix: {'/'.join(_CI_GH_PREFIXES)})"
            ),
            remedy=(
                f"regenerate a read:packages-only classic PAT, "
                f"set {_REGISTRY_TOKEN_VAR} to the new token, and redeploy"
            ),
        )
    status_code = _ci_probe_registry_token(token)
    if status_code is None:
        return CapProbeResult(
            status="unknown",
            evidence="registry probe could not run (network unreachable or timeout)",
        )
    if status_code in (401, 403):
        return CapProbeResult(
            status="red",
            evidence=f"{_REGISTRY_TOKEN_VAR} rejected by GitHub (HTTP {status_code})",
            remedy=(
                f"registry token rejected by GitHub — rotate {_REGISTRY_TOKEN_VAR} "
                "(regenerate a read:packages-only classic PAT) and redeploy"
            ),
        )
    if status_code >= 400:
        return CapProbeResult(
            status="unknown",
            evidence=f"registry probe returned HTTP {status_code} — validity unproven",
        )
    return CapProbeResult(status="green", evidence=f"HTTP {status_code}")


def _probe_sandbox_image() -> CapProbeResult:
    """Check that the per-task sandbox Docker image is present and pullable.

    Module-level for test patching. Never raises; returns ``unknown`` on infra
    failure (e.g. Docker daemon not running — FR-007: infra failure ≠ red)."""
    image = _SANDBOX_IMAGE
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return CapProbeResult(
            status="unknown",
            evidence=f"docker inspect could not run: {exc}",
        )
    if inspect.returncode == 0:
        return CapProbeResult(status="green", evidence="image present locally")
    # Not cached locally — try a pull to distinguish "missing" from "daemon down"
    try:
        pull = subprocess.run(
            ["docker", "pull", image, "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        return CapProbeResult(
            status="unknown",
            evidence=f"docker pull could not run: {exc}",
        )
    if pull.returncode == 0:
        return CapProbeResult(status="green", evidence="image pulled successfully")
    stderr = (pull.stderr or "").strip()[:200]
    return CapProbeResult(
        status="red",
        evidence=f"docker pull failed: {stderr}",
        remedy=(
            f"sandbox image {image!r} is not available — "
            "pre-pull it ('docker pull <image>') or rebuild the image "
            "('make image'), then resume_goal to retry dispatch"
        ),
    )


#: Registry of probe runners, keyed by capability id.  Module-level so tests
#: can monkeypatch individual entries (``env_cap._PROBE_RUNNERS[cap_id] = fake``).
_PROBE_RUNNERS: dict[str, Callable[[], CapProbeResult]] = {
    "registry:npm-github": _probe_registry_npm_github,
    "sandbox:image": _probe_sandbox_image,
}


# ---- sweep runner (called from tick_all, never from per-goal ticks) ----------

def run_if_stale(store: "StateStore", cap_id: str) -> CapProbeResult:
    """Run the probe for ``cap_id`` if the cached result is stale; return the
    cached result when it is still fresh.

    Never raises — a probe exception degrades to ``unknown`` which is cached
    and read by the next tick (FR-007: unknown ≠ hold, so goals are not
    held for an unrunnable probe)."""
    if not _is_stale(store, cap_id):
        cached = read_result(store, cap_id)
        if cached is not None:
            return cached
    runner = _PROBE_RUNNERS.get(cap_id)
    if runner is None:
        result = CapProbeResult(
            status="unknown",
            evidence=f"capability id {cap_id!r} not recognised by this instance",
        )
    else:
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001
            result = CapProbeResult(status="unknown", evidence=f"probe raised: {exc}")
    _write_result(store, cap_id, result)
    return result


def refresh_needed(store: "StateStore", needed_caps: frozenset) -> None:
    """Refresh all stale probes for the capabilities in ``needed_caps``.

    Called ONCE per heartbeat sweep before the per-goal ticks (FR-004: the
    tick path reads persisted rows, it never probes networks). An empty
    ``needed_caps`` is a no-op (zero I/O — common when no project uses
    capability gating).

    Best-effort: a single probe failure is swallowed; its cached result
    stays ``unknown`` (FR-007: fail-open on infra uncertainty)."""
    for cap_id in needed_caps:
        try:
            run_if_stale(store, cap_id)
        except Exception:  # noqa: BLE001
            pass


def red_caps_for(
    store: "StateStore", declared: "tuple[str, ...]",
) -> "list[CapProbeResult]":
    """Return the CapProbeResults that are ``red`` for the declared capabilities.

    Reads only persisted meta rows (zero network I/O). An absent or
    ``unknown`` result is fail-open (FR-007): only a confirmed ``red`` holds
    dispatch."""
    red: list[CapProbeResult] = []
    for cap_id in declared:
        result = read_result(store, cap_id)
        if result is not None and result.status == "red":
            red.append(result)
    return red
