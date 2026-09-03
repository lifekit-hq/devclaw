"""Environment-capability probes — spec 030 FR-001/FR-004/FR-006.

A capability probe is a zero-LLM check with a stable id (e.g.
``registry:npm-github``) that produces green/red/unknown + evidence. Results
are TTL-cached in the state-store meta table; the sweep runner refreshes them
at most once per heartbeat sweep (:func:`probe_ttl_s`), and only when at least one
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
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Optional, Protocol

from . import config as _config
from .engine.sandcastle import REGISTRY_TOKEN_VAR as _REGISTRY_TOKEN_VAR
from .engine.sandcastle import SANDBOX_IMAGE as _SANDBOX_IMAGE


class MetaStore(Protocol):
    """The only store surface a probe needs: two control-plane meta rows.

    Structural on purpose — the sweep runner holds a ``StateStore`` while the
    tick path holds a ``GoalStore`` (which passes both through to the same
    shared store). Typing this as either concrete class is what let the first
    cut of this module compile while raising ``AttributeError`` in production."""

    def get_meta(self, key: str) -> Optional[str]: ...
    def set_meta(self, key: str, value: str) -> None: ...


# ---- constants ---------------------------------------------------------------

_META_PREFIX = "env_cap_probe:"

#: The v1 capability ids (FR-006), as constants because they are cross-surface
#: identifiers, not local literals: a project's ``devclaw.json`` declares them,
#: the ``mechanical:env`` block names them, and doctor's findings must name the
#: SAME string so the operator reads ONE story rather than two (US3). Anything
#: that speaks about a capability imports these — never a re-typed literal.
CAP_REGISTRY_NPM_GITHUB = "registry:npm-github"
CAP_SANDBOX_IMAGE = "sandbox:image"
#: spec 032 (Q3): a project's verification environment is its own CI. A
#: registered project carries this capability IMPLICITLY (the registry map
#: adds it — one place, ``GoalService._registered_capabilities``); red when
#: the default branch carries no ``.github/workflows`` — not dispatchable
#: until onboarding writes one.
CAP_CI_DEFINITION = "ci:definition"

#: capability ids this instance can probe (spec 030 FR-006 + spec 032).
KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    CAP_REGISTRY_NPM_GITHUB, CAP_SANDBOX_IMAGE, CAP_CI_DEFINITION,
})

#: spec 032 US2: a WORKER-REPORTED environment deficiency is recorded as a
#: red capability row under this prefix (``worker:<slug of the item>``), so
#: admission, the hold, the heal, doctor and get_goal tell the same story
#: they tell for a declared capability. It has no probe runner: the row is
#: red while the instance's environment is the one the worker reported
#: against (:func:`instance_env_ref`) and reads green once that changes.
WORKER_PREFIX = "worker:"

CapScope = Literal["instance", "project"]

#: What each capability's answer is ABOUT. An ``instance``-scoped capability has
#: one answer for the whole fleet — the registry credential is a process-wide
#: env var, so probing it twice can only produce the same result. A ``project``
#: one is about a value the owning project resolves for itself: ``sandbox:image``
#: is about the image THAT project's sandbox launches, and a project may pin its
#: own (``projects.sandbox_image``, ADR 0005). Caching those under one
#: fleet-wide key answers about the wrong image in both directions — a project
#: pinning ``devclaw-sandbox-dotnet:local`` would be admitted because the
#: DEFAULT image is pullable, and held because the default one isn't. The scope
#: is what :func:`_meta_key` keys on, so a new capability declares its scope
#: here rather than each caller remembering to pass a project id.
CAP_SCOPES: dict[str, CapScope] = {
    CAP_REGISTRY_NPM_GITHUB: "instance",
    CAP_SANDBOX_IMAGE: "project",
    CAP_CI_DEFINITION: "project",
}


def instance_env_ref() -> str:
    """The identity of the environment a worker runs in, as far as this arc
    can tell it: the sandbox image ref and the devclaw build. A worker-reported
    deficiency is pinned to the ref it was reported against and heals when the
    ref changes — a new image or runner IS the fix arriving. (US4 adds the
    project's environment declaration to this identity.)"""
    return f"{_SANDBOX_IMAGE}|{_config.git_sha() or ''}"


def worker_cap_id(item: str) -> str:
    """``worker:<slug>`` for a reported item — stable across re-reports of
    the same gap so one item is one row (and one catalog entry)."""
    slug = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-")[:60] or "unknown"
    return f"{WORKER_PREFIX}{slug}"


def _is_project_scoped(cap_id: str) -> bool:
    return CAP_SCOPES.get(cap_id) == "project" or cap_id.startswith(WORKER_PREFIX)

#: GitHub token prefixes. A `read:packages` credential that matches none of
#: these is not a GitHub token at all — the exact 2026-08-31 failure, where a
#: malformed secret rode the whole plumbing into the sandbox and only
#: surfaced as an `npm ci` 401 after it had eaten a goal's dispatch budget.
GH_TOKEN_PREFIXES = ("ghp_", "github_pat_", "ghs_", "gho_")

#: Remedy for the credential being absent while a project declares the
#: capability. Shared with doctor so both surfaces print the same fix (US3).
REGISTRY_UNSET_REMEDY = (
    f"set {_REGISTRY_TOKEN_VAR} to a read:packages-only classic PAT "
    f"(`gh secret set {_REGISTRY_TOKEN_VAR}`) and redeploy, or drop "
    f"{CAP_REGISTRY_NPM_GITHUB!r} from the project's devclaw.json if it no "
    "longer depends on a private registry"
)

# ---- data types --------------------------------------------------------------

CapStatus = Literal["green", "red", "unknown"]


@dataclass(frozen=True)
class CapProbeResult:
    """The outcome of one capability probe run."""
    status: CapStatus
    evidence: str = ""
    #: Human-readable fix action; non-empty only when status is ``red``.
    remedy: str = ""


@dataclass(frozen=True)
class CapTarget:
    """One probe run: the capability, and — when it is project-scoped — whose.

    ``subject`` is the resolved per-project value the probe is about (for
    ``sandbox:image``, the image ref that project's sandbox will launch).
    The SWEEP caller resolves it, not this module: env_cap must not reach into
    the project registry, and the per-goal read path needs only the cache key
    (``cap_id`` + ``project_id``), never the subject. ``None`` ⇒ the instance
    default."""
    cap_id: str
    project_id: Optional[str] = None
    subject: Optional[str] = None


# ---- meta-table helpers ------------------------------------------------------

def _meta_key(cap_id: str, project_id: Optional[str] = None) -> str:
    """The meta row a result is cached under — per project for a
    project-scoped capability (:data:`CAP_SCOPES`), fleet-wide otherwise.

    A project-scoped capability with no owning project (an ad-hoc goal) keys
    fleet-wide on purpose: no project means no override, so the probe really
    is about the instance default."""
    if project_id and _is_project_scoped(cap_id):
        return f"{_META_PREFIX}{cap_id}@{project_id}"
    return f"{_META_PREFIX}{cap_id}"


def read_result(
    store: MetaStore, cap_id: str, project_id: Optional[str] = None,
) -> Optional[CapProbeResult]:
    """Read the last persisted probe result for ``cap_id``.

    Returns ``None`` if the capability has never been probed.  The admission
    gate treats ``None`` as ``unknown``, which is fail-open (FR-007)."""
    raw = store.get_meta(_meta_key(cap_id, project_id))
    if not raw:
        return None
    try:
        d = json.loads(raw)
        result = CapProbeResult(
            status=d.get("status", "unknown"),
            evidence=d.get("evidence", ""),
            remedy=d.get("remedy", ""),
        )
    except Exception:  # noqa: BLE001
        return None
    if cap_id.startswith(WORKER_PREFIX) and result.status == "red":
        # spec 032 US2: the row is about the environment it was reported
        # against; a changed environment reads green (the fix arrived).
        if (d.get("env_ref") or "") != instance_env_ref():
            return CapProbeResult(
                status="green",
                evidence=f"environment changed since the worker's report ({result.evidence})",
            )
    return result


def _worker_index_key(project_id: Optional[str]) -> str:
    return f"{_META_PREFIX}worker-index@{project_id or ''}"


def worker_caps_for(store: MetaStore, project_id: Optional[str]) -> "tuple[str, ...]":
    """The worker-reported capability ids recorded for ``project_id``."""
    raw = store.get_meta(_worker_index_key(project_id))
    if not raw:
        return ()
    try:
        ids = json.loads(raw)
    except Exception:  # noqa: BLE001
        return ()
    return tuple(i for i in ids if isinstance(i, str) and i.startswith(WORKER_PREFIX))


def record_worker_deficiency(
    store: MetaStore, project_id: Optional[str], item: str,
    *, goal_id: str = "", task_id: str = "",
) -> str:
    """Record a worker-reported environment deficiency (spec 032 US2) as a
    red capability row for the project and return its capability id. One
    row per (item, project): a repeat report against the same environment
    refreshes ``probed_at_ms`` only; a report against a NEW environment
    re-pins the row (the earlier fix did not close the gap)."""
    from .state_store import _now_ms  # deferred — avoids circular at module load

    cap_id = worker_cap_id(item)
    pid = (project_id or "").strip() or None
    key = _meta_key(cap_id, pid)
    ref = instance_env_ref()
    existing: dict = {}
    raw = store.get_meta(key)
    if raw:
        try:
            existing = json.loads(raw)
        except Exception:  # noqa: BLE001
            existing = {}
    if existing.get("env_ref") == ref:
        existing["probed_at_ms"] = _now_ms()
        store.set_meta(key, json.dumps(existing))
    else:
        store.set_meta(key, json.dumps({
            "status": "red",
            "evidence": (
                f"a worker reported the sandbox lacks: {item}"
                + (f" (goal {goal_id}" + (f", task {task_id}" if task_id else "") + ")" if goal_id else "")
            ),
            "remedy": (
                f"provide {item!r} in the sandbox — devclaw work (the image, a mise tool, "
                "or the project's environment declaration); the hold clears when the "
                "instance's environment changes, or resume_goal after fixing it by hand"
            ),
            "env_ref": ref,
            "probed_at_ms": _now_ms(),
        }))
    ids = set(worker_caps_for(store, pid))
    if cap_id not in ids:
        ids.add(cap_id)
        store.set_meta(_worker_index_key(pid), json.dumps(sorted(ids)))
    return cap_id


def _write_result(store: MetaStore, target: CapTarget, result: CapProbeResult) -> None:
    from .state_store import _now_ms  # deferred — avoids circular at module load
    raw = json.dumps({
        "status": result.status,
        "evidence": result.evidence,
        "remedy": result.remedy,
        "probed_at_ms": _now_ms(),
    })
    store.set_meta(_meta_key(target.cap_id, target.project_id), raw)


def probe_ttl_s() -> int:
    """How long a cached probe result stays fresh — half the heartbeat cadence.

    Derived, never fixed: the TTL's only job is to make a result last exactly
    one sweep, so it has to move when the cadence does. A constant wider than
    the heartbeat (the 16-min literal this replaces, against a 15-min default)
    keeps a RED row fresh through the sweep that follows the fix, which turns
    FR-004's "auto-resume within ~one sweep" into two — and any operator who
    tightens ``DEVCLAW_GOAL_TICK_SECONDS`` stretches that gap further rather
    than shrinking it. Halving guarantees staleness by the next sweep with
    room for cadence jitter; the floor only keeps a nonsensical cadence from
    producing a zero TTL."""
    return max(1, _config.goal_tick_seconds() // 2)


def _is_stale(store: MetaStore, target: CapTarget) -> bool:
    """True when there is no cached result or it is older than :func:`probe_ttl_s`."""
    raw = store.get_meta(_meta_key(target.cap_id, target.project_id))
    if not raw:
        return True
    try:
        d = json.loads(raw)
        from .state_store import _now_ms
        age_ms = _now_ms() - int(d.get("probed_at_ms", 0))
        return age_ms > probe_ttl_s() * 1000
    except Exception:  # noqa: BLE001
        return True


# ---- probe implementations (module-level for test patching) ------------------

def probe_registry_token(token: str, timeout_s: float = 5.0) -> Optional[int]:
    """HTTP status from an authenticated GitHub API call, or None if the
    probe could not run at all. The token is never logged or returned.

    Lives here rather than in doctor because the capability layer and the
    doctor check are two READERS of one probe primitive; doctor imports it
    (both re-export it as a module global so tests patch it in the calling
    module, per the collector convention). Never raises: every failure
    degrades to None — an unverifiable credential is ``unknown``, never OK
    and never red (FR-007)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "devclaw-doctor"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def _probe_registry_npm_github(target: CapTarget) -> CapProbeResult:
    """Probe the GitHub Packages npm-registry credential (the fs-479 class).

    Runs the same HTTP check doctor's ``instance.registry.token`` reports on
    (:func:`probe_registry_token` — the FR-006 reference implementation), so
    the two surfaces can never disagree about the credential. Module-level so
    tests can patch it. Never raises; ``unknown`` on infra failure (FR-007).

    UNSET is RED here, unlike the instance-level doctor check. The probe runs
    at all only because a project DECLARED this capability, and a declared
    dependency that is absent is not an uncertainty — `npm ci` against GitHub
    Packages 401s deterministically, which is precisely the session burn
    SC-002 exists to prevent. Doctor's check is instance-scoped, so it has no
    declaration to read and keeps unset as the supported pre-token posture
    unless some registered project declares the capability."""
    token = os.environ.get(_REGISTRY_TOKEN_VAR, "").strip()
    if not token:
        return CapProbeResult(
            status="red",
            evidence=(
                f"{_REGISTRY_TOKEN_VAR} is not set, but this project declares "
                f"{CAP_REGISTRY_NPM_GITHUB!r} — no credential reaches the sandbox "
                "and an install against GitHub Packages will 401 there"
            ),
            remedy=REGISTRY_UNSET_REMEDY,
        )
    if not token.startswith(GH_TOKEN_PREFIXES):
        return CapProbeResult(
            status="red",
            evidence=(
                f"{_REGISTRY_TOKEN_VAR} is set but is not a GitHub token "
                f"(expected prefix: {'/'.join(GH_TOKEN_PREFIXES)})"
            ),
            remedy=(
                f"regenerate a read:packages-only classic PAT, "
                f"set {_REGISTRY_TOKEN_VAR} to the new token, and redeploy"
            ),
        )
    status_code = probe_registry_token(token)
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


def _probe_sandbox_image(target: CapTarget) -> CapProbeResult:
    """Check that the per-task sandbox Docker image is present and pullable.

    Probes the image THIS project's sandbox will actually launch — its
    ``sandbox_image`` pin (ADR 0005) when it has one, resolved by the sweep and
    carried on ``target.subject``, else the instance default. This capability
    is project-scoped (:data:`CAP_SCOPES`) for exactly that reason: one
    fleet-wide answer would be about an image the project never runs.

    Module-level for test patching. Never raises; returns ``unknown`` on infra
    failure (e.g. Docker daemon not running — FR-007: infra failure ≠ red)."""
    image = target.subject or _SANDBOX_IMAGE
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
        return CapProbeResult(status="green", evidence=f"image {image!r} present locally")
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
        return CapProbeResult(status="green", evidence=f"image {image!r} pulled successfully")
    stderr = (pull.stderr or "").strip()[:200]
    return CapProbeResult(
        status="red",
        evidence=f"docker pull of {image!r} failed: {stderr}",
        remedy=(
            f"sandbox image {image!r} is not available — "
            "pre-pull it ('docker pull <image>') or rebuild the image "
            "('make image'), then resume_goal to retry dispatch"
        ),
    )


_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

CI_DEFINITION_REMEDY = (
    "the project has no CI definition on its default branch — its own CI is the "
    "verification environment (spec 032): run onboarding, which writes "
    ".github/workflows/verify.yml around the manifest's verifyCmd, or add one by hand"
)


def _probe_ci_definition(target: CapTarget) -> CapProbeResult:
    """Does the project's default branch carry a CI definition? ``subject`` is
    the project's repo url (resolved by the sweep). One bounded ``gh api``
    read; HTTP 404 on the workflows directory is RED (no definition), any
    other failure is ``unknown`` (FR-007). Module-level for test patching."""
    repo = (target.subject or "").strip()
    m = _OWNER_REPO_RE.search(repo) if repo else None
    if not m:
        return CapProbeResult(status="unknown", evidence=f"no GitHub repo url on the project ({repo!r})")
    owner_repo = f"{m.group(1)}/{m.group(2)}"
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/contents/.github/workflows", "--jq", "length"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return CapProbeResult(status="unknown", evidence=f"gh could not run: {exc}")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode == 0 and out.isdigit():
        if int(out) > 0:
            return CapProbeResult(status="green", evidence=f"{out} workflow file(s) on the default branch")
        return CapProbeResult(status="red", evidence=".github/workflows is empty", remedy=CI_DEFINITION_REMEDY)
    if "404" in err or "Not Found" in err:
        return CapProbeResult(status="red", evidence="no .github/workflows on the default branch", remedy=CI_DEFINITION_REMEDY)
    return CapProbeResult(status="unknown", evidence=f"workflows read failed: {(err or out)[:160]}")


#: Registry of probe runners, keyed by capability id.  Module-level so tests
#: can monkeypatch individual entries (``env_cap._PROBE_RUNNERS[cap_id] = fake``).
_PROBE_RUNNERS: dict[str, Callable[[CapTarget], CapProbeResult]] = {
    CAP_REGISTRY_NPM_GITHUB: _probe_registry_npm_github,
    CAP_SANDBOX_IMAGE: _probe_sandbox_image,
    CAP_CI_DEFINITION: _probe_ci_definition,
}


# ---- sweep runner (called from tick_all, never from per-goal ticks) ----------

def run_if_stale(store: MetaStore, target: CapTarget) -> CapProbeResult:
    """Run ``target``'s probe if its cached result is stale; return the cached
    result when it is still fresh.

    Never raises — a probe exception degrades to ``unknown`` which is cached
    and read by the next tick (FR-007: unknown ≠ hold, so goals are not
    held for an unrunnable probe)."""
    if target.cap_id.startswith(WORKER_PREFIX):
        # spec 032 US2: no runner — the row is the worker's report, pinned to
        # an environment ref; it is never re-probed, only re-read.
        return read_result(store, target.cap_id, target.project_id) or CapProbeResult("unknown")
    if not _is_stale(store, target):
        cached = read_result(store, target.cap_id, target.project_id)
        if cached is not None:
            return cached
    runner = _PROBE_RUNNERS.get(target.cap_id)
    if runner is None:
        result = CapProbeResult(
            status="unknown",
            evidence=f"capability id {target.cap_id!r} not recognised by this instance",
        )
    else:
        try:
            result = runner(target)
        except Exception as exc:  # noqa: BLE001
            result = CapProbeResult(status="unknown", evidence=f"probe raised: {exc}")
    _write_result(store, target, result)
    return result


def refresh_needed(store: MetaStore, targets: "Iterable[CapTarget]") -> None:
    """Refresh every stale probe in ``targets``.

    Called ONCE per heartbeat sweep before the per-goal ticks (FR-004: the
    tick path reads persisted rows, it never probes networks). An empty
    ``targets`` is a no-op (zero I/O — common when no project uses capability
    gating). Deduplication is the caller's job: it owns the scope rule that
    decides whether two projects share one target (see :data:`CAP_SCOPES`).

    Best-effort: a single probe failure is swallowed; its cached result
    stays ``unknown`` (FR-007: fail-open on infra uncertainty)."""
    for target in targets:
        try:
            run_if_stale(store, target)
        except Exception:  # noqa: BLE001
            pass


def red_caps_for(
    store: MetaStore, declared: "tuple[str, ...]",
    project_id: Optional[str] = None,
) -> "list[tuple[str, CapProbeResult]]":
    """Return the ``(capability id, result)`` pairs that are ``red`` for the
    declared capabilities.

    Reads only persisted meta rows (zero network I/O). An absent or
    ``unknown`` result is fail-open (FR-007): only a confirmed ``red`` holds
    dispatch. The id rides along because the operator-facing block must name
    the same probe id doctor reports (FR/US3) — probe evidence alone does not
    carry it. ``project_id`` selects the per-project row for a project-scoped
    capability (:data:`CAP_SCOPES`) — a goal must be admitted against the probe
    of the image ITS sandbox launches, not the fleet default's."""
    red: list[tuple[str, CapProbeResult]] = []
    # spec 032 US2: a worker-reported deficiency holds the project whether or
    # not anything is declared — the worker's report IS the evidence.
    for cap_id in tuple(declared) + worker_caps_for(store, (project_id or "").strip() or None):
        result = read_result(store, cap_id, (project_id or "").strip() or None)
        if result is not None and result.status == "red":
            red.append((cap_id, result))
    return red
